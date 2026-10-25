"""Transcribe speech with Gemma 4 E2B on LiteRT-LM, fully offline (the headset's "ears" on the Mac).

    python3 litert/ears.py clip.wav                 # print what was said
    python3 litert/ears.py --bench a.wav b.wav      # one load, latency per clip
    python3 litert/ears.py --serve                  # JSON lines on stdin/stdout, used by headset --ears litert

Serve protocol: first line out is {"ready": true, "load_s": ...} or {"error": "..."}. Then each line in
{"wav_b64": "..."} gets one line out {"text": "...", "seconds": ...} or {"error": "..."}.

This file imports on Python 3.9; the litert_lm package itself needs Python 3.10+.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from pathlib import Path

DEFAULT_MODEL = os.environ.get("JM_LITERT_MODEL", str(Path.home() / "models" / "gemma-4-E2B-it.litertlm"))
CACHE_DIR = os.environ.get("JM_LITERT_CACHE", str(Path.home() / ".cache" / "journeyman-litert"))
SYSTEM = ("Transcribe the user's speech exactly, in English. Output only the words spoken, no quotes, no notes. "
          "If there is no speech, output nothing.")
ASK = "Transcribe this audio."
NO_SPEECH = re.compile(r"^\W*(no speech|silence|inaudible|\[.*\]|\(.*\))\W*$", re.I)


def clean(raw: str) -> str:
    text = " ".join((raw or "").replace("*", "").split()).strip().strip('"').strip()
    text = re.sub(r"^(transcription|transcript)\s*:\s*", "", text, flags=re.I)
    return "" if NO_SPEECH.match(text) else text


def _message_text(msg) -> str:
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict):
        return "".join(c.get("text", "") for c in msg.get("content", []) if isinstance(c, dict))
    return "".join(getattr(c, "text", "") or "" for c in getattr(msg, "contents", []) or []) or str(msg)


class Ears:
    """Holds one loaded LiteRT-LM engine; each clip gets a fresh conversation."""

    def __init__(self, model_path: str = DEFAULT_MODEL, threads: int | None = None, engine=None):
        self.engine = engine or self._load(model_path, threads)

    @staticmethod
    def _load(model_path, threads):
        try:
            import litert_lm
        except ImportError:
            raise RuntimeError("litert_lm is not installed (needs Python 3.10+): see litert/README.md")
        if not Path(model_path).is_file():
            raise RuntimeError(f"model not found: {model_path} (set JM_LITERT_MODEL or --model)")
        os.makedirs(CACHE_DIR, exist_ok=True)
        cpu = litert_lm.Backend.CPU(thread_count=threads)
        return litert_lm.Engine(model_path, backend=cpu, audio_backend=litert_lm.Backend.CPU(),
                                max_num_tokens=2048, cache_dir=CACHE_DIR)

    def _conversation(self):
        try:
            import litert_lm
            sampler = litert_lm.SamplerConfig(temperature=0.0, top_k=1)
        except ImportError:
            sampler = None
        return self.engine.create_conversation(system_message=SYSTEM, sampler_config=sampler, max_output_tokens=128)

    def transcribe(self, wav: bytes) -> dict:
        t = time.time()
        conv = self._conversation()
        try:
            raw = _message_text(conv.send_message({"role": "user", "content": [
                {"type": "audio", "blob": base64.b64encode(wav).decode()}, {"type": "text", "text": ASK}]}))
        finally:
            close = getattr(conv, "close", None)
            if close:
                close()
        return {"text": clean(raw), "seconds": round(time.time() - t, 2)}


def serve(ears_factory, stdin=None, stdout=None) -> int:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout

    def say(obj):
        stdout.write(json.dumps(obj) + "\n")
        stdout.flush()

    t = time.time()
    try:
        ears = ears_factory()
    except Exception as e:
        say({"error": str(e)})
        return 2
    say({"ready": True, "load_s": round(time.time() - t, 2)})
    for line in stdin:
        if not line.strip():
            continue
        try:
            say(ears.transcribe(base64.b64decode(json.loads(line)["wav_b64"])))
        except Exception as e:
            say({"error": f"{type(e).__name__}: {e}"})
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Transcribe speech with Gemma 4 E2B on LiteRT-LM.")
    p.add_argument("wav", nargs="*", help="16 kHz mono WAV files (<= 30 s each)")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f".litertlm file (default {DEFAULT_MODEL})")
    p.add_argument("--threads", type=int, default=None)
    p.add_argument("--serve", action="store_true", help="JSON lines on stdin/stdout (for the headset)")
    p.add_argument("--bench", action="store_true", help="print load and per-clip latency")
    a = p.parse_args(argv)
    if a.serve:
        out = os.fdopen(os.dup(1), "w")
        os.dup2(2, 1)
        return serve(lambda: Ears(a.model, a.threads), stdout=out)
    t = time.time()
    try:
        ears = Ears(a.model, a.threads)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.bench:
        print(f"[load {time.time() - t:.2f}s]")
    for path in a.wav:
        r = ears.transcribe(Path(path).read_bytes())
        print(f"[{r['seconds']:.2f}s] {path}: {r['text']}" if a.bench else r["text"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
