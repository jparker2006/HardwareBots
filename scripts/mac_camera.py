#!/usr/bin/env python3
"""Mac camera for `jm serve` without OpenCV: ffmpeg (avfoundation) grabs JPEGs and POSTs them to /api/frames.

  python3 scripts/mac_camera.py --list                         # cameras ffmpeg can see
  python3 scripts/mac_camera.py --camera auto                  # the Brio if plugged in, else the built-in camera
  python3 scripts/mac_camera.py --camera 1 --jm http://127.0.0.1:3000 --fps 1

The first run asks for camera permission for the terminal app (System Settings > Privacy & Security > Camera).
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from journeyman.eyes import split_jpegs  # noqa: E402
from journeyman.local_http import request_json  # noqa: E402

SIZES = (["-framerate", "30", "-video_size", "1280x720"], ["-framerate", "30"], [])


def list_cameras(ffmpeg: str = "ffmpeg") -> list:
    """[(index, name)] of avfoundation video devices; screens ("Capture screen 0") are left out."""
    p = subprocess.run([ffmpeg, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                       capture_output=True, text=True, timeout=15)
    return parse_devices(p.stderr)


def parse_devices(text: str) -> list:
    out, video = [], False
    for line in text.splitlines():
        if "video devices" in line:
            video = True
        elif "audio devices" in line:
            video = False
        elif video:
            m = re.search(r"\]\s*\[(\d+)\]\s*(.+)$", line)
            if m and not m.group(2).lower().startswith("capture screen"):
                out.append((m.group(1), m.group(2).strip()))
    return out


def pick(cameras: list, wanted: str = "auto") -> tuple | None:
    """An index, part of a name, or auto (a Brio first, then the first camera)."""
    if wanted.isdigit():
        return next((c for c in cameras if c[0] == wanted), (wanted, "camera " + wanted))
    key = "brio" if wanted == "auto" else wanted.lower()
    hit = next((c for c in cameras if key in c[1].lower()), None)
    return hit or (cameras[0] if cameras and wanted == "auto" else None)


def command(index: str, fps: float, width: int, size: list, ffmpeg: str = "ffmpeg") -> list:
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "avfoundation"] + size + [
        "-i", f"{index}:none", "-vf", f"fps={fps:g},scale={width}:-2", "-q:v", "5",
        "-f", "image2pipe", "-c:v", "mjpeg", "-"]


def run(cmd: list, jm: str) -> int:
    """Stream one ffmpeg run into Journeyman; returns how many frames were pushed."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    buf, sent = bytearray(), 0
    try:
        while True:
            chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(65536)
            if not chunk:
                break
            buf.extend(chunk)
            for jpeg in split_jpegs(buf):
                try:
                    request_json(jm.rstrip("/") + "/api/frames", {"jpeg_b64": base64.b64encode(jpeg).decode()},
                                 timeout=5)
                    sent += 1
                    if sent == 1:
                        print(f"camera: first frame pushed ({len(jpeg)} bytes)", flush=True)
                except Exception as e:
                    print(f"camera: push failed ({e}); is jm serve up at {jm}?", flush=True)
                    time.sleep(1)
    finally:
        proc.kill()
        err = proc.stderr.read().decode(errors="replace").strip()
        proc.wait()
    if not sent and err:
        print("camera: ffmpeg: " + err[-300:], flush=True)
    return sent


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--camera", default=os.environ.get("JM_MAC_CAMERA", "auto"), help="index, part of a name, or auto")
    p.add_argument("--jm", default="http://127.0.0.1:" + os.environ.get("JM_PORT", "3000"))
    p.add_argument("--fps", type=float, default=float(os.environ.get("JM_MAC_FPS", "1")))
    p.add_argument("--width", type=int, default=int(os.environ.get("JM_FRAME_WIDTH", "512")))
    p.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg")
    p.add_argument("--list", action="store_true", help="list cameras and exit")
    a = p.parse_args(argv)
    cameras = list_cameras(a.ffmpeg)
    if a.list:
        for i, name in cameras:
            print(f"{i}  {name}")
        return 0 if cameras else 1
    cam = pick(cameras, a.camera)
    if not cam:
        print(f"camera: no camera matches {a.camera!r}; seen: {cameras or 'none'}", flush=True)
        return 1
    print(f"camera: [{cam[0]}] {cam[1]} -> {a.jm}/api/frames at {a.fps:g} fps", flush=True)
    while True:
        for size in SIZES:
            if run(command(cam[0], a.fps, a.width, size, a.ffmpeg), a.jm):
                break
        time.sleep(2)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
