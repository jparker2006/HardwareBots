#!/usr/bin/env python3
"""Journeyman headset for the Mac: mic → speech detection → Thor; Thor's replies → macOS `say`.

  python3 headset/headset.py                  # voice, hands-free; finds Thor (USB-C link first, then venue Wi-Fi)
  python3 headset/headset.py --ptt            # push-to-talk: Enter starts, Enter stops
  python3 headset/headset.py --text           # type instead of talking
  python3 headset/headset.py --thor http://192.168.55.1:3000     # one fixed address (or several, comma-separated)
  python3 headset/headset.py --list-devices   # then --device 2 or --device "USB"
  python3 headset/headset.py --ears litert    # Gemma E2B on the Mac (LiteRT-LM) turns speech into text; Thor gets text

The mic is ignored while Journeyman speaks. If Thor restarts, the headset reconnects by itself. If the address
in use stops answering (say Wi-Fi is turned off), it switches to the next one that answers and says so.
Needs `pip3 install sounddevice numpy` for voice mode. Text mode needs nothing.
`--ears litert` runs litert/ears.py in the LiteRT venv (see litert/README.md); if that can't start, audio goes to Thor.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import wave

RATE = 16000
BLOCK = 480  # 30 ms
THOR = "http://192.168.55.1:3000,http://192.168.3.131:3000"

try:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from journeyman.local_http import NotLocal, request_json
except ImportError:
    class NotLocal(Exception):
        pass

    def request_json(url: str, body: dict | None = None, timeout: float = 30.0) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)


class Console:
    """Prints log lines over a one-line live mic meter without garbling either."""

    def __init__(self, stream=None):
        self.out = stream or sys.stdout
        self.tty = self.out.isatty()
        self.lock = threading.Lock()
        self.shown = False
        self.last = 0.0

    def line(self, text: str) -> None:
        with self.lock:
            if self.shown:
                self.out.write("\r\x1b[K")
                self.shown = False
            self.out.write(text + "\n")
            self.out.flush()

    def meter(self, text: str) -> None:
        now = time.time()
        if not self.tty or now - self.last < 0.1:
            return
        with self.lock:
            self.out.write("\r\x1b[K" + text)
            self.out.flush()
            self.shown, self.last = True, now

    def clear(self) -> None:
        with self.lock:
            if self.shown:
                self.out.write("\r\x1b[K")
                self.out.flush()
                self.shown = False


def meter(level: float, threshold: float, state: str = "", width: int = 24) -> str:
    """`mic [#####·····|········] 1234/900 REC` on a log scale; `|` marks the speech threshold."""
    def pos(v: float) -> int:
        return min(width - 1, max(0, int(math.log10(max(v, 1.0)) / 4.0 * width)))
    bar = ["#" if i <= pos(level) and level >= 1 else "·" for i in range(width)]
    bar[pos(threshold)] = "|"
    return f"mic [{''.join(bar)}] {level:5.0f}/{threshold:.0f} {state}".rstrip()


def addresses(spec: str) -> list:
    return [a.strip().rstrip("/") for a in spec.split(",") if a.strip()]


def describe(url: str) -> str:
    return f"USB-C link ({url})" if "//192.168.55." in url else f"network ({url})"


def reachable(url: str, timeout: float = 1.5) -> bool:
    try:
        request_json(url + "/api/ops/status", {}, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def pick(urls: list, probe=reachable) -> str | None:
    """The first address that answers, in order (USB-C link first by default)."""
    for url in urls:
        if probe(url):
            return url
    return None


class Link:
    """Talks to Thor. Says once when Thor goes away and once when it is back, instead of spamming errors.

    With several addresses it uses the first that answers, and moves to another one when that stops answering.
    """

    def __init__(self, thor: str, console: Console, probe=reachable):
        self.urls, self.console, self.probe = addresses(thor), console, probe
        self.thor = self.urls[0]
        self.up: bool | None = None
        self._next_pick = 0.0
        self._pick_lock = threading.Lock()

    def choose(self, announce: bool = True) -> bool:
        url = pick(self.urls, self.probe)
        if url is None:
            return False
        if url != self.thor or announce:
            self.console.line(f"[thor] {'using' if announce else 'switched to'} {describe(url)}"
                              + ("" if url == self.urls[0] else f"; {self.urls[0]} not answering"))
        self.thor = url
        return True

    def failover(self) -> None:
        if len(self.urls) < 2 or not self._pick_lock.acquire(blocking=False):
            return
        try:
            if time.time() >= self._next_pick:
                self._next_pick = time.time() + 2.0
                self.choose(announce=False)
        finally:
            self._pick_lock.release()

    def _mark(self, ok: bool, err: Exception | None = None) -> None:
        if ok and self.up is False:
            self.console.line("[thor] reconnected")
        elif not ok and self.up is not False:
            self.console.line(f"[thor] unreachable ({err}); retrying quietly")
        self.up = ok

    def call(self, path: str, body: dict | None = None, timeout: float = 30.0, retry_s: float = 0.0) -> dict:
        deadline = time.time() + retry_s
        while True:
            try:
                out = request_json(self.thor + path, body, timeout=timeout)
                self._mark(True)
                return out
            except (urllib.error.HTTPError, NotLocal):
                self._mark(True)
                raise
            except Exception as e:
                self._mark(False, e)
                self.failover()
                if time.time() >= deadline:
                    raise
                time.sleep(0.5)


class Timing:
    """Remembers when you stopped talking so the reply can report how long it took."""

    def __init__(self):
        self.ended_at: float | None = None

    def ended(self, t: float) -> None:
        self.ended_at = t

    def reply_started(self, now: float | None = None) -> float | None:
        if self.ended_at is None:
            return None
        dt, self.ended_at = (now or time.time()) - self.ended_at, None
        return dt


def resync(seq: int, current: int) -> int:
    """After Thor restarts with a fresh journal its seq starts over; follow it down so new replies aren't skipped."""
    return min(seq, current)


class Speaker:
    """Plays Thor's replies with `say`. While it speaks, the mic is ignored (no echo)."""

    def __init__(self, link: Link, timing: Timing, voice: str | None = None, rate: int | None = None):
        self.link, self.timing, self.voice, self.rate = link, timing, voice, rate
        self.busy_until = 0.0

    def busy(self) -> bool:
        return time.time() < self.busy_until

    def command(self, text: str) -> list:
        return (["say"] + (["-v", self.voice] if self.voice else []) + (["-r", str(self.rate)] if self.rate else [])
                + [text])

    def run(self) -> None:
        seq = None
        while True:
            try:
                current = self.link.call("/api/headset/outbox?after=-1", timeout=10)["seq"]
                seq = current if seq is None else resync(seq, current)
                while True:
                    out = self.link.call(f"/api/headset/outbox?after={seq}", timeout=30)
                    seq = out["seq"]
                    for item in out["items"]:
                        self.play(item)
            except Exception:
                time.sleep(1)

    def play(self, item: dict) -> None:
        dt = self.timing.reply_started()
        took = f"   [{dt:.1f}s after you stopped]" if dt is not None else ""
        self.link.console.line(f"journeyman: {item['text']}{took}")
        self.busy_until = time.time() + 600
        try:
            subprocess.run(self.command(item["text"]), check=False)
        except FileNotFoundError:
            pass
        self.busy_until = time.time() + 0.4
        try:
            self.link.call("/api/headset/said", {"id": item["id"]}, timeout=5)
        except Exception:
            pass


def to_wav(samples) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def tell_hearing(link: Link) -> None:
    """Lets the screen show "hearing you" while you are still talking."""
    def send():
        try:
            link.call("/api/headset/voice", {"state": "hearing"}, timeout=3)
        except Exception:
            pass
    threading.Thread(target=send, daemon=True).start()


LITERT_PYTHON = os.path.expanduser(os.environ.get("JM_LITERT_PYTHON", "~/jm-litert/bin/python"))
EARS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "litert", "ears.py")


class LiteRTEars:
    """Gemma 4 E2B on the Mac via LiteRT-LM (litert/ears.py --serve in its own venv). Speech in, text out.

    If it can't start, or a clip fails, `transcribe` returns None and the headset sends that clip's audio to Thor.
    """

    def __init__(self, console: Console, python: str = LITERT_PYTHON, script: str = EARS, popen=subprocess.Popen):
        self.console, self.lock, self.proc = console, threading.Lock(), None
        try:
            self.proc = popen([python, script, "--serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True, bufsize=1)
            first = json.loads(self.proc.stdout.readline() or '{"error": "ears exited at start"}')
            if not first.get("ready"):
                raise RuntimeError(first.get("error", "not ready"))
            console.line(f"[ears] Gemma E2B on LiteRT-LM ready ({first.get('load_s', 0):.1f}s load); Thor gets text")
        except Exception as e:
            self.close()
            console.line(f"[ears] LiteRT-LM unavailable ({e}); sending audio to Thor instead. See litert/README.md")

    @property
    def ok(self) -> bool:
        return self.proc is not None

    def transcribe(self, wav: bytes) -> dict | None:
        if not self.ok:
            return None
        with self.lock:
            try:
                self.proc.stdin.write(json.dumps({"wav_b64": base64.b64encode(wav).decode()}) + "\n")
                self.proc.stdin.flush()
                out = json.loads(self.proc.stdout.readline() or '{"error": "ears exited"}')
            except Exception as e:
                out = {"error": str(e)}
        if "error" in out:
            self.console.line(f"[ears] {out['error']}; sending this clip's audio to Thor")
            if self.proc.poll() is not None:
                self.close()
            return None
        return out

    def close(self) -> None:
        if self.proc is not None:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None


def send_clip(link: Link, timing: Timing, samples, speech_end: float, note: str = "", ears=None) -> None:
    seconds = len(samples) / RATE
    t0 = speech_end - seconds
    timing.ended(speech_end)
    heard = ears.transcribe(to_wav(samples)) if ears is not None else None
    if heard is not None and not heard["text"]:
        timing.ended_at = None
        link.console.line(f"[ears] no words in {seconds:.1f}s clip ({heard['seconds']:.1f}s), not sent")
        return
    try:
        if heard is not None:
            link.call("/api/headset/utterance", {"text": heard["text"], "t0": t0, "t1": speech_end,
                                                 "heard_by": "gemma-4-e2b-litert"}, retry_s=5)
            link.console.line(f"you (gemma e2b, {heard['seconds']:.1f}s): {heard['text']}{note}")
            return
        link.call("/api/headset/utterance", {"wav_b64": base64.b64encode(to_wav(samples)).decode(),
                                             "t0": t0, "t1": speech_end}, retry_s=5)
        link.console.line(f"[sent {seconds:.1f}s of speech{note}]")
    except Exception as e:
        timing.ended_at = None
        link.console.line(f"[send failed, {seconds:.1f}s dropped] {e}")


def pick_device(spec: str | None):
    if spec is None:
        return None
    return int(spec) if spec.isdigit() else spec


def list_devices() -> None:
    import sounddevice as sd
    print(sd.query_devices())
    print("\nuse --device <number> or --device <part of the name>; `>` marks the default input")


def listen(link: Link, speaker: Speaker, timing: Timing, args, ears=None) -> None:
    import sounddevice as sd
    from vad import Vad

    con = link.console
    vad = Vad(threshold=args.threshold, rate=RATE, end_silence_s=args.end_silence, max_s=args.max_seconds)
    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK,
                        device=pick_device(args.device)) as stream:
        if args.threshold is None:
            con.line("calibrating room noise for 1 s, stay quiet...")
            noise, _ = stream.read(RATE)
            vad.calibrate(noise[:, 0])
        con.line(f"listening (noise {vad.floor:.0f}, threshold {vad.threshold:.0f}). "
                 "Too loud in here? restart with --ptt. Ctrl-C to quit.")
        was_active = False
        while True:
            block, _ = stream.read(BLOCK)
            block = block[:, 0].copy()
            if speaker.busy():
                vad.reset()
                con.meter(meter(0, vad.threshold, "(journeyman speaking)"))
                continue
            utt = vad.feed(block)
            if vad.active and not was_active:
                tell_hearing(link)
            was_active = vad.active
            con.meter(meter(vad.level, vad.threshold, "REC" if vad.active else ""))
            if utt is not None:
                note = ", cut at --max-seconds" if utt.cut else ""
                threading.Thread(target=send_clip, args=(link, timing, utt.samples, time.time() - utt.silence_s, note, ears),
                                 daemon=True).start()


def push_to_talk(link: Link, speaker: Speaker, timing: Timing, args, ears=None) -> None:
    import numpy as np
    import sounddevice as sd
    from vad import rms

    con = link.console
    rec = {"on": False, "blocks": []}
    max_n = int(args.max_seconds * RATE)

    def callback(indata, frames, t, status):
        if rec["on"]:
            block = indata[:, 0].copy()
            rec["blocks"].append(block)
            con.meter(meter(rms(block), args.threshold or 300.0, "REC — Enter to send"))

    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK,
                        device=pick_device(args.device), callback=callback):
        con.line("push-to-talk: Enter starts recording, Enter again sends. Ctrl-C to quit.")
        while True:
            input()
            if speaker.busy():
                con.line("(journeyman is still talking; recording anyway)")
            rec["blocks"], rec["on"] = [], True
            tell_hearing(link)
            con.line("recording... Enter to send")
            input()
            rec["on"] = False
            end = time.time()
            con.clear()
            blocks = rec["blocks"]
            if not blocks:
                con.line("[nothing recorded]")
                continue
            samples = np.concatenate(blocks)
            note = ""
            if len(samples) > max_n:
                samples, note = samples[:max_n], f", cut to first {args.max_seconds:.0f}s"
            threading.Thread(target=send_clip, args=(link, timing, samples, end, note, ears), daemon=True).start()


def type_loop(link: Link, timing: Timing) -> None:
    print("type what you'd say; Enter sends. Ctrl-C to quit.")
    while True:
        text = input("> ").strip()
        if text:
            now = time.time()
            timing.ended(now)
            try:
                link.call("/api/headset/utterance", {"text": text, "t0": now, "t1": now}, retry_s=5)
            except Exception as e:
                timing.ended_at = None
                print(f"[send failed] {e}")


def main(argv: list | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--thor", default=os.environ.get("JM_THOR", THOR),
                   help="journeyman service URL on Thor; comma-separated = try in order (default: USB-C link, then Wi-Fi)")
    p.add_argument("--text", action="store_true", help="type instead of talking")
    p.add_argument("--ptt", action="store_true", help="push-to-talk: Enter starts, Enter stops (for loud rooms)")
    p.add_argument("--list-devices", action="store_true", help="list audio devices and exit")
    p.add_argument("--device", help="mic to use: number or part of the name from --list-devices")
    p.add_argument("--voice", help="macOS voice for `say`, e.g. Samantha")
    p.add_argument("--rate", type=int, help="speaking rate for `say`, words per minute (e.g. 200)")
    p.add_argument("--threshold", type=float, help="speech loudness threshold (default: 3x room noise, adaptive)")
    p.add_argument("--end-silence", type=float, default=0.8, help="seconds of quiet that end an utterance")
    p.add_argument("--max-seconds", type=float, default=15.0, help="longest clip sent (Gemma's limit is 30)")
    p.add_argument("--ears", choices=["thor", "litert"], default=os.environ.get("JM_EARS", "thor"),
                   help="who turns speech into text: thor (send audio) or litert (Gemma E2B on this Mac)")
    p.add_argument("--litert-python", default=LITERT_PYTHON, help="python of the LiteRT-LM venv (for --ears litert)")
    args = p.parse_args(argv)
    if args.list_devices:
        return list_devices()
    console = Console()
    link = Link(args.thor, console)
    if not link.choose():
        console.line(f"[thor] none of {', '.join(link.urls)} answering; will keep trying, starting with {link.thor}")
    try:
        mode = link.call("/api/ops/status", {}, timeout=5)["result"]["mode"]
        console.line(f"journeyman status: {mode}")
    except Exception:
        console.line(f"Thor at {link.thor} isn't answering yet; carrying on, will connect when it's up")
    timing = Timing()
    speaker = Speaker(link, timing, args.voice, args.rate)
    threading.Thread(target=speaker.run, daemon=True).start()
    ears = LiteRTEars(console, os.path.expanduser(args.litert_python)) if args.ears == "litert" and not args.text else None
    try:
        if args.text:
            type_loop(link, timing)
        elif args.ptt:
            push_to_talk(link, speaker, timing, args, ears)
        else:
            listen(link, speaker, timing, args, ears)
    except (KeyboardInterrupt, EOFError):
        console.clear()
        print()
    finally:
        if ears is not None:
            ears.close()


if __name__ == "__main__":
    main()
