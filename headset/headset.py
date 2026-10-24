#!/usr/bin/env python3
"""Journeyman headset for the Mac: mic → speech detection → Thor; Thor's replies → macOS `say`.

  python3 headset/headset.py --thor http://192.168.55.1:3000          # voice
  python3 headset/headset.py --thor http://192.168.55.1:3000 --text   # type instead of talking

Needs `pip3 install sounddevice numpy` for voice mode. Text mode needs nothing.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import threading
import time
import urllib.request
import wave

RATE = 16000
BLOCK = 480  # 30 ms


def post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get(url: str, timeout: float = 30) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


class Speaker:
    """Plays Thor's replies with `say`. While it speaks, the mic is ignored (no echo)."""

    def __init__(self, thor: str, voice: str | None):
        self.thor, self.voice = thor, voice
        self.busy_until = 0.0

    def busy(self) -> bool:
        return time.time() < self.busy_until

    def run(self) -> None:
        seq = get(f"{self.thor}/api/headset/outbox?after=-1")["seq"]
        while True:
            try:
                out = get(f"{self.thor}/api/headset/outbox?after={seq}", timeout=30)
            except Exception as e:
                print(f"[speaker] {e}; retrying")
                time.sleep(1)
                continue
            seq = out["seq"]
            for item in out["items"]:
                print(f"journeyman: {item['text']}")
                self.busy_until = time.time() + 600
                cmd = ["say"] + (["-v", self.voice] if self.voice else []) + [item["text"]]
                subprocess.run(cmd, check=False)
                self.busy_until = time.time() + 0.4
                try:
                    post(f"{self.thor}/api/headset/said", {"id": item["id"]})
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


def listen(thor: str, speaker: Speaker, args) -> None:
    import numpy as np
    import sounddevice as sd

    print("calibrating noise for 1 s, stay quiet...")
    noise = sd.rec(RATE, samplerate=RATE, channels=1, dtype="int16")
    sd.wait()
    floor = float(np.sqrt(np.mean(noise.astype(np.float32) ** 2))) or 50.0
    threshold = args.threshold or max(300.0, floor * 3.0)
    print(f"listening (noise {floor:.0f}, threshold {threshold:.0f}). Ctrl-C to quit.")

    pre_roll, voiced, silent, blocks, started_at = [], 0, 0, [], None
    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK) as stream:
        while True:
            block, _ = stream.read(BLOCK)
            block = block[:, 0].copy()
            if speaker.busy():
                pre_roll, voiced, blocks, started_at = [], 0, [], None
                continue
            level = float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))
            loud = level > threshold
            if started_at is None:
                pre_roll = (pre_roll + [block])[-10:]
                voiced = voiced + 1 if loud else 0
                if voiced >= 5:
                    started_at, blocks, silent = time.time() - 0.45, list(pre_roll), 0
                continue
            blocks.append(block)
            silent = 0 if loud else silent + 1
            too_long = time.time() - started_at > args.max_seconds
            if silent * BLOCK / RATE >= args.end_silence or too_long:
                t1 = time.time()
                wav = to_wav(np.concatenate(blocks))
                threading.Thread(target=send_clip, args=(thor, wav, started_at, t1), daemon=True).start()
                pre_roll, voiced, blocks, started_at = [], 0, [], None


def send_clip(thor: str, wav: bytes, t0: float, t1: float) -> None:
    try:
        post(f"{thor}/api/headset/utterance", {"wav_b64": base64.b64encode(wav).decode(), "t0": t0, "t1": t1})
        print(f"[sent {t1 - t0:.1f}s of speech]")
    except Exception as e:
        print(f"[send failed] {e}")


def type_loop(thor: str) -> None:
    print("type what you'd say; Enter sends. Ctrl-C to quit.")
    while True:
        text = input("> ").strip()
        if text:
            now = time.time()
            post(f"{thor}/api/headset/utterance", {"text": text, "t0": now, "t1": now})


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--thor", default="http://192.168.55.1:3000", help="journeyman service URL on Thor")
    p.add_argument("--text", action="store_true", help="type instead of talking")
    p.add_argument("--voice", help="macOS voice for `say`, e.g. Samantha")
    p.add_argument("--threshold", type=float, help="speech loudness threshold (default: 3x room noise)")
    p.add_argument("--end-silence", type=float, default=0.8, help="seconds of quiet that end an utterance")
    p.add_argument("--max-seconds", type=float, default=15.0, help="longest clip sent (Gemma's limit is 30)")
    args = p.parse_args()
    thor = args.thor.rstrip("/")
    print(f"journeyman status: {post(thor + '/api/ops/status', {})['result']['mode']}")
    speaker = Speaker(thor, args.voice)
    threading.Thread(target=speaker.run, daemon=True).start()
    try:
        type_loop(thor) if args.text else listen(thor, speaker, args)
    except (KeyboardInterrupt, EOFError):
        print()


if __name__ == "__main__":
    main()
