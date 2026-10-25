"""`jm doctor`: the setup checklist for bringing up Gemma on Thor. Each check says PASS, WARN or FAIL and what to do next."""
from __future__ import annotations

import base64
import glob
import io
import json
import math
import os
import shutil
import statistics
import struct
import sys
import urllib.error
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Config
from .gemma import Gemma, audio_part, image_part, text_part
from .local_http import internet_reachable, request_json

PORTS = [(8000, "vLLM"), (8080, "llama.cpp"), (11434, "Ollama")]

RED_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAA0JCgsKCA0LCgsODg0PEyAVExISEyccHhcgLikxMC4pLSwzOko+MzZGNywtQFdBRkxOUlNSMj5a"
    "YVpQYEpRUk//2wBDAQ4ODhMREyYVFSZPNS01T09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT0//wAAR"
    "CABAAEADASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFgEBAQEAAAAAAAAAAAAAAAAA"
    "AAUG/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAwDAQACEQMRAD8AjAJrbAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAP/9k=")

TOOL = {"type": "function", "function": {
    "name": "get_time", "description": "Get the current time in a city.",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}
TOOL_HINT = ("start vLLM with --enable-auto-tool-choice --tool-call-parser gemma4, or llama.cpp server with --jinja; "
             "otherwise the agent falls back to text")


@dataclass
class Check:
    name: str
    status: str
    detail: str
    hint: str = ""


def tone_wav(seconds: float = 1.0, rate: int = 16000, hz: float = 440.0) -> bytes:
    frames = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * hz * i / rate)))
                      for i in range(int(seconds * rate)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


def _err(e: Exception) -> str:
    if isinstance(e, urllib.error.URLError) and not isinstance(e, urllib.error.HTTPError):
        return str(e.reason)
    body = ""
    if hasattr(e, "read"):
        try:
            body = e.read().decode(errors="replace")[:160]
        except Exception:
            pass
    return f"{e} {body}".strip()


def chat(url: str, model: str, content, tools=None, max_tokens: int = 40) -> tuple[dict, float]:
    gemma = Gemma(url, model)
    gemma.timeouts["default"] = 120.0
    return gemma.chat([{"role": "user", "content": content}], tools=tools, max_tokens=max_tokens, temperature=0)


def list_models(url: str, timeout: float = 3.0) -> list[str]:
    return [m["id"] for m in request_json(url.rstrip("/") + "/models", timeout=timeout).get("data", [])]


def candidates() -> list[tuple[str, str]]:
    return [(f"http://localhost:{port}/v1", name) for port, name in PORTS]


def find_gemma(env_url: str | None, probes: list[tuple[str, str]] | None = None,
               current: str = "") -> tuple[Check, str, list[str]]:
    tried = []
    for url, label in ([(env_url, "JM_GEMMA_URL")] if env_url else []) + (probes if probes is not None else candidates()):
        try:
            models = list_models(url)
        except Exception as e:
            tried.append(f"{url} ({label}): {_err(e)[:60]}")
            continue
        hint = "" if url.rstrip("/") in (env_url or "", current.rstrip("/")) else f"export JM_GEMMA_URL={url}"
        status = "PASS" if models and not hint else "WARN"
        if not models:
            hint = "server answered but lists no models; check the model finished loading"
        return Check("gemma server", status, f"{url} ({label}) models={models}", hint), url, models
    return (Check("gemma server", "FAIL", "nothing answered: " + "; ".join(tried),
                  "start Gemma (vLLM :8000, llama.cpp :8080, Ollama :11434) or set JM_GEMMA_URL=http://<host>:<port>/v1 "
                  "(from the Mac over USB-C Thor is often 192.168.55.1)"),
            "", [])


def pick_model(models: list[str], wanted: str) -> tuple[str, Check | None]:
    if wanted and wanted not in models:
        return wanted, Check("gemma model", "WARN", f"JM_GEMMA_MODEL={wanted} not in {models}",
                             f"export JM_GEMMA_MODEL={models[0] if models else '<id>'}")
    return wanted or (models[0] if models else ""), None


def check_python() -> Check:
    v = sys.version_info
    ok = v >= (3, 9)
    return Check("python", "PASS" if ok else "FAIL", f"{v.major}.{v.minor}.{v.micro}", "" if ok else "use Python 3.9+")


def check_text(url: str, model: str) -> Check:
    try:
        msg, secs = chat(url, model, "Say OK.", max_tokens=5)
    except Exception as e:
        return Check("text round trip", "FAIL", _err(e), "check the server log; the model may still be loading")
    detail = f"{secs:.2f}s {(msg.get('content') or '')[:40]!r}"
    if secs > 5:
        return Check("text round trip", "WARN", detail, "slow; the first call warms up, run `jm bench` for steady numbers")
    return Check("text round trip", "PASS", detail)


def check_image(url: str, model: str) -> Check:
    try:
        msg, secs = chat(url, model, [image_part(RED_JPEG), text_part("What color is this image? One word.")])
    except Exception as e:
        return Check("image input", "FAIL", _err(e), "serve a vision-capable Gemma 4 (any size) with image input enabled")
    return Check("image input", "PASS", f"{secs:.2f}s {(msg.get('content') or '')[:40]!r} (expected: red)")


def check_audio(url: str, model: str) -> Check:
    try:
        msg, secs = chat(url, model, [audio_part(tone_wav()), text_part("Describe this audio in a few words.")])
    except Exception as e:
        return Check("audio input", "FAIL", _err(e), "audio needs vLLM with E2B/E4B")
    return Check("audio input", "PASS", f"{secs:.2f}s {(msg.get('content') or '')[:40]!r}")


def check_tools(url: str, model: str) -> Check:
    try:
        msg, secs = chat(url, model, "What time is it in Paris? Use the tool.", tools=[TOOL], max_tokens=60)
    except Exception as e:
        return Check("tool calling", "FAIL", _err(e), TOOL_HINT)
    calls = msg.get("tool_calls") or []
    if calls:
        return Check("tool calling", "PASS", f"{secs:.2f}s called {calls[0].get('function', {}).get('name')}")
    return Check("tool calling", "WARN", f"answered in text: {(msg.get('content') or '')[:40]!r}", TOOL_HINT)


def check_camera(cam: str) -> Check:
    devices = sorted(glob.glob("/dev/video*"))
    listed = f"devices={devices or 'none'}"
    if cam in ("", "none", "off"):
        return Check("camera", "WARN", f"JM_CAMERA={cam!r} (pushed frames only) {listed}", "set JM_CAMERA=0 to use the Brio")
    try:
        import cv2  # type: ignore
    except Exception:
        cv2 = None
    if cv2 is not None:
        cap = cv2.VideoCapture(int(cam) if cam.isdigit() else cam)
        ok = cap.isOpened() and cap.read()[0]
        cap.release()
        if ok:
            return Check("camera", "PASS", f"opencv opened JM_CAMERA={cam} {listed}")
        return Check("camera", "FAIL", f"opencv could not read JM_CAMERA={cam} {listed}",
                     "plug in the Brio, pick the right index with JM_CAMERA, check `v4l2-ctl --list-devices`")
    if shutil.which("gst-launch-1.0"):
        device = f"/dev/video{cam}" if cam.isdigit() else cam
        if device in devices or not cam.isdigit():
            return Check("camera", "PASS", f"gstreamer will read {device} (not opened by doctor) {listed}")
        return Check("camera", "FAIL", f"gstreamer present but {device} missing {listed}", "plug in the Brio or set JM_CAMERA")
    return Check("camera", "FAIL", f"no OpenCV and no gst-launch-1.0 {listed}",
                 "pip install opencv-python, or apt install gstreamer1.0-tools; or push frames from the Mac")


def check_service(url: str) -> Check:
    try:
        status = request_json(f"{url}/api/ops/status", {}, timeout=3)["result"]
    except Exception as e:
        return Check("journeyman service", "WARN", f"{url} not running ({_err(e)[:60]})", "start it with `./jm serve`")
    camera = (status.get("camera") or {}).get("source")
    return Check("journeyman service", "PASS", f"{url} mode={status.get('mode')} camera={camera}")


def check_internet(offline: bool, online: bool | None = None) -> Check:
    online = internet_reachable() if online is None else online
    if not online:
        return Check("internet", "PASS", "unreachable (offline)")
    return Check("internet", "FAIL" if offline else "WARN", "reachable", "turn Wi-Fi/Ethernet uplink off for the demo")


def check_disk(data_dir: Path) -> Check:
    path = Path(data_dir).resolve()
    while not path.exists():
        path = path.parent
    free = shutil.disk_usage(str(path)).free / 1e9
    detail = f"{free:.1f} GB free at {path}"
    if free < 1:
        return Check("disk", "FAIL", detail, "free space or point JM_DATA at a bigger disk")
    if free < 5:
        return Check("disk", "WARN", detail, "frames and audio add up; free some space")
    return Check("disk", "PASS", detail)


def run(cfg: Config, jm_url: str, offline: bool = False, env_url: str | None = None,
        probes: list[tuple[str, str]] | None = None, online: bool | None = None) -> list[Check]:
    checks = [check_python()]
    env_url = os.environ.get("JM_GEMMA_URL") if env_url is None else env_url
    found, url, models = find_gemma(env_url and env_url.rstrip("/"), probes, cfg.gemma_url)
    checks.append(found)
    if url:
        model, warn = pick_model(models, cfg.gemma_model)
        if warn:
            checks.append(warn)
        checks += [check_text(url, model), check_image(url, model), check_audio(url, model), check_tools(url, model)]
    checks += [check_camera(cfg.camera), check_service(jm_url), check_internet(offline, online), check_disk(cfg.data_dir)]
    return checks


def failed(checks: list[Check]) -> bool:
    return any(c.status == "FAIL" for c in checks)


def render(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        lines.append(f"{c.status:<5} {c.name:<19} {c.detail}")
        if c.hint and c.status != "PASS":
            lines.append(f"{'':<6}{'':<19} -> {c.hint}")
    counts = {s: sum(c.status == s for c in checks) for s in ("PASS", "WARN", "FAIL")}
    lines.append(f"\n{counts['PASS']} pass, {counts['WARN']} warn, {counts['FAIL']} fail")
    return "\n".join(lines)


def to_json(checks: list[Check]) -> str:
    return json.dumps({"ok": not failed(checks), "checks": [asdict(c) for c in checks]}, indent=2)


def bench(url: str, model: str, runs: int = 3, jpeg: bytes | None = None, wav: bytes | None = None) -> list[dict]:
    probes = [("text", "Say OK.", None, 5),
              ("image", [image_part(jpeg or RED_JPEG), text_part("What is in this image? One sentence.")], None, 40),
              ("audio", [audio_part(wav or tone_wav()), text_part("Transcribe exactly.")], None, 100),
              ("tools", "What time is it in Paris? Use the tool.", [TOOL], 60)]
    results = []
    for name, content, tools, max_tokens in probes:
        times, error, reply = [], "", ""
        for _ in range(runs):
            try:
                msg, secs = chat(url, model, content, tools=tools, max_tokens=max_tokens)
            except Exception as e:
                error = _err(e)[:120]
                break
            times.append(secs)
            reply = msg.get("content") or ("tool_calls" if msg.get("tool_calls") else "")
        results.append({"probe": name, "runs": len(times), "median_s": round(statistics.median(times), 3) if times else None,
                        "min_s": round(min(times), 3) if times else None, "reply": reply[:60], "error": error})
    return results


def render_bench(model: str, results: list[dict]) -> str:
    lines = [f"model {model}"]
    for r in results:
        if r["runs"]:
            lines.append(f"{r['probe']:<6} median {r['median_s']:.2f}s  min {r['min_s']:.2f}s  ({r['runs']} runs)  {r['reply']!r}")
        else:
            lines.append(f"{r['probe']:<6} ERROR {r['error']}")
    return "\n".join(lines)
