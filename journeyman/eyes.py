"""Camera capture, keyframe selection and the `look` sense.

Without OpenCV the camera is read by gst-launch-1.0: JPEGs on one pipe and 32x18 gray thumbnails on another.
A frame is kept when its thumbnail differs from the last kept one, or every HEARTBEAT_S seconds.
"""
from __future__ import annotations

import fcntl
import glob
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from fractions import Fraction
from pathlib import Path

from .gemma import image_part, text_part

try:
    import cv2  # type: ignore
except Exception:  # OpenCV is optional; GStreamer or pushed frames work without it
    cv2 = None

THUMB_W, THUMB_H = 32, 18
CHANGE_THRESHOLD = 3.0
HEARTBEAT_S = 5.0
SOURCE_SIZE = (1280, 720)

VIDIOC_QUERYCAP, VIDIOC_ENUM_FMT = 0x80685600, 0xC0405602
CAP_VIDEO_CAPTURE, CAP_META_CAPTURE, CAP_DEVICE_CAPS = 0x1, 0x00800000, 0x80000000


def probe(device: str) -> dict:
    """What a /dev/video node is: card name, capture or metadata, and its pixel formats (e.g. MJPG, YUYV)."""
    info = {"device": device, "card": "", "capture": None, "formats": []}
    try:
        fd = os.open(device, os.O_RDWR | os.O_NONBLOCK)
    except OSError as e:
        info["error"] = e.strerror or str(e)
        return info
    try:
        buf = fcntl.ioctl(fd, VIDIOC_QUERYCAP, bytearray(104))
        card = bytes(buf[16:48]).split(b"\0")[0].decode(errors="replace")
        caps, dev_caps = struct.unpack_from("<II", buf, 84)
        caps = dev_caps if caps & CAP_DEVICE_CAPS else caps
        info.update(card=card, capture=bool(caps & CAP_VIDEO_CAPTURE), meta=bool(caps & CAP_META_CAPTURE))
        for i in range(32):
            try:
                desc = fcntl.ioctl(fd, VIDIOC_ENUM_FMT, struct.pack("<II", i, 1) + bytes(56))
            except OSError:
                break
            info["formats"].append(bytes(desc[44:48]).decode(errors="replace").strip())
    except OSError as e:
        info["error"] = e.strerror or str(e)
    finally:
        os.close(fd)
    return info


def list_devices() -> list[str]:
    return sorted(glob.glob("/dev/video*"), key=lambda d: int(re.sub(r"\D", "", d) or 0))


def pick_device(cam: str, devices: list[str] | None = None, probe_fn=None) -> dict:
    """The capture node for JM_CAMERA: an index, a path or `auto`. Metadata nodes (the Brio's /dev/video1) are skipped."""
    devices = list_devices() if devices is None else devices
    probe_fn = probe_fn or probe
    wanted = f"/dev/video{cam}" if cam.isdigit() else ("" if cam == "auto" else cam)
    first = probe_fn(wanted) if wanted else None
    if first and first.get("capture") is not False and "error" not in first:
        return first
    capture = [i for i in (probe_fn(d) for d in devices if d != wanted) if i.get("capture")]
    same_card = [i for i in capture if first and i.get("card") == first.get("card")]
    if capture:
        return (same_card or capture)[0]
    return first or {"device": wanted or (devices[0] if devices else "/dev/video0"), "capture": None, "formats": [],
                     "error": "no capture device found"}


def formats_to_try(formats: list[str]) -> list[str]:
    """MJPEG first (the Brio's fast path), raw as fallback; unknown formats try both."""
    if formats and "MJPG" not in formats:
        return ["raw"]
    return ["mjpeg", "raw"]


def pipeline(device: str, fmt: str = "mjpeg", width: int | None = 512, fps: float | None = 1.0,
             source: tuple = SOURCE_SIZE, thumbs: bool = True, num_buffers: int = 0, verbose: bool = False,
             fds: tuple = (3, 4)) -> list[str]:
    """gst-launch-1.0 argv: JPEGs to fds[0] and, with thumbs, GRAY8 thumbnails to fds[1]."""
    cmd = ["gst-launch-1.0", "-v" if verbose else "-q", "v4l2src", f"device={device}"]
    if num_buffers:
        cmd.append(f"num-buffers={num_buffers}")
    if fmt == "mjpeg":
        cmd += ["!", f"image/jpeg,width={source[0]},height={source[1]}", "!", "jpegdec"]
    else:
        cmd += ["!", "video/x-raw"]
    cmd += ["!", "videoconvert"]
    if width:
        cmd += ["!", "videoscale", "!", f"video/x-raw,width={width},pixel-aspect-ratio=1/1"]
    if fps:
        rate = Fraction(fps).limit_denominator(100)
        cmd += ["!", "videorate", "drop-only=true", "!", f"video/x-raw,framerate={rate.numerator}/{rate.denominator}"]
    jpeg = ["jpegenc", "quality=80", "!", "fdsink", f"fd={fds[0]}"]
    if not thumbs:
        return cmd + ["!"] + jpeg
    return cmd + ["!", "tee", "name=t", "t.", "!", "queue", "!"] + jpeg + [
        "t.", "!", "queue", "!", "videoscale", "!", "videoconvert", "!",
        f"video/x-raw,format=GRAY8,width={THUMB_W},height={THUMB_H}", "!", "fdsink", f"fd={fds[1]}"]


def split_jpegs(buf: bytearray) -> list[bytes]:
    """Pop every complete JPEG (SOI..EOI) off the front of buf; a partial one stays for the next read."""
    out = []
    while True:
        start = buf.find(b"\xff\xd8")
        if start < 0:
            del buf[:max(0, len(buf) - 1)]
            return out
        end = buf.find(b"\xff\xd9", start + 2)
        if end < 0:
            del buf[:start]
            return out
        out.append(bytes(buf[start:end + 2]))
        del buf[:end + 2]


def jpeg_size(data: bytes) -> tuple | None:
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker, length = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + length
    return None


def difference(a: bytes | None, b: bytes | None) -> float:
    """Mean absolute difference of two gray thumbnails (0-255); unknown or mismatched counts as changed."""
    if not a or not b or len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b)) / float(len(a))


def read_all(fd: int, on_chunk) -> None:
    try:
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                return
            on_chunk(chunk)
    finally:
        os.close(fd)


def errors(proc) -> str:
    proc.errlog.seek(0)
    return proc.errlog.read().decode(errors="replace").strip()


def spawn(device: str, fmt: str, stdout=subprocess.DEVNULL, **kw):
    """Start gst-launch writing JPEGs and thumbs into two pipes; returns (proc, jpeg_read_fd, thumb_read_fd)."""
    jr, jw = os.pipe()
    tr, tw = os.pipe()
    cmd = pipeline(device, fmt, fds=(jw, tw), **kw)
    errlog = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdout=stdout, stderr=errlog, pass_fds=(jw, tw))
    proc.errlog = errlog
    os.close(jw)
    os.close(tw)
    return proc, jr, tr


def camera_test(cam: str, out: Path, frames: int = 30) -> dict:
    """Grab a few full-size frames from the capture node, save the last as a JPEG, report format and fps."""
    found = pick_device(cam)
    report = {"devices": [probe(d) for d in list_devices()], "device": found["device"], "card": found.get("card"),
              "formats": found.get("formats"), "ok": False}
    if not shutil.which("gst-launch-1.0"):
        report["error"] = "gst-launch-1.0 not found (apt install gstreamer1.0-tools gstreamer1.0-plugins-good)"
        return report
    for fmt in formats_to_try(found.get("formats") or []):
        log = tempfile.TemporaryFile()
        proc, jfd, tfd = spawn(found["device"], fmt, stdout=log, width=None, fps=None, thumbs=False,
                               num_buffers=frames, verbose=True)
        os.close(tfd)
        buf, got, stamps = bytearray(), [], []

        def take(chunk):
            buf.extend(chunk)
            for j in split_jpegs(buf):
                got.append(j)
                stamps.append(time.time())
        reader = threading.Thread(target=read_all, args=(jfd, take), daemon=True)
        reader.start()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        reader.join(2)
        log.seek(0)
        caps = re.findall(r"v4l2src0\.GstPad:src: caps = (.+)", log.read().decode(errors="replace"))
        report.setdefault("attempts", []).append({"format": fmt, "frames": len(got), "exit": proc.returncode,
                                                  "stderr": errors(proc)[-300:]})
        if got:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(got[-1])
            rate = re.search(r"framerate=\(fraction\)(\d+)/(\d+)", caps[-1]) if caps else None
            report.update(ok=True, format=fmt, caps=caps[-1].strip() if caps else None, frames=len(got),
                          fps_negotiated=round(int(rate.group(1)) / max(1, int(rate.group(2))), 2) if rate else None,
                          fps_measured=round((len(stamps) - 1) / (stamps[-1] - stamps[0]), 1)
                          if len(stamps) > 2 and stamps[-1] > stamps[0] else None,
                          snapshot=str(out), snapshot_bytes=len(got[-1]), snapshot_size=jpeg_size(got[-1]))
            return report
    report["error"] = "no frames from " + found["device"]
    return report


class Eyes:
    def __init__(self, core):
        self.core = core
        self.cfg = core.config
        self.latest: bytes | None = None
        self.latest_at = 0.0
        self.source = "none"
        self._last_small: bytes | None = None
        self._last_kept_at = 0.0
        self._thumb: bytes | None = None
        self.seen = self.kept = 0
        self._stop = threading.Event()

    # ---- capture --------------------------------------------------------
    def start(self) -> None:
        cam = self.cfg.camera
        if cam in ("", "none", "off"):
            self.source = "pushed frames only"
            return
        if cv2 is not None:
            threading.Thread(target=self._opencv_loop, args=(cam,), daemon=True).start()
        elif shutil.which("gst-launch-1.0"):
            threading.Thread(target=self._gst_loop, args=(cam,), daemon=True).start()
        else:
            self.source = "no camera library (install opencv-python or GStreamer); pushed frames only"

    def _opencv_loop(self, cam: str) -> None:
        cap = cv2.VideoCapture(int(cam) if cam.isdigit() else cam)
        if not cap.isOpened():
            self.source = f"camera {cam} did not open"
            return
        self.source = f"opencv camera {cam}"
        while not self._stop.is_set():
            ok, frame = cap.read()
            if ok:
                h, w = frame.shape[:2]
                scale = self.cfg.frame_width / float(w)
                frame = cv2.resize(frame, (self.cfg.frame_width, int(h * scale)))
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    small = cv2.cvtColor(cv2.resize(frame, (THUMB_W, THUMB_H)), cv2.COLOR_BGR2GRAY)
                    self.ingest(buf.tobytes(), small=small.tobytes())
            time.sleep(self.cfg.frame_interval_s)

    def _gst_loop(self, cam: str) -> None:
        found = pick_device(cam)
        while not self._stop.is_set():
            for fmt in formats_to_try(found.get("formats") or []):
                if self._run_gst(found, fmt) or self._stop.is_set():
                    break
            time.sleep(2)

    def _run_gst(self, found: dict, fmt: str) -> int:
        device, card = found["device"], found.get("card") or "camera"
        fps = 1.0 / max(0.05, self.cfg.frame_interval_s)
        proc, jfd, tfd = spawn(device, fmt, width=self.cfg.frame_width, fps=fps)
        self.source = f"gstreamer {device} ({card}) {fmt} starting"
        size, tbuf, jbuf, count = THUMB_W * THUMB_H, bytearray(), bytearray(), [0]

        def thumbs(chunk):
            tbuf.extend(chunk)
            while len(tbuf) >= size:
                self._thumb = bytes(tbuf[:size])
                del tbuf[:size]

        def jpegs(chunk):
            jbuf.extend(chunk)
            for j in split_jpegs(jbuf):
                count[0] += 1
                self.source = f"gstreamer {device} ({card}) {fmt} {self.cfg.frame_width}px @ {fps:g} fps"
                self.ingest(j, small=self._thumb)
        threading.Thread(target=read_all, args=(tfd, thumbs), daemon=True).start()
        read_all(jfd, jpegs)
        proc.wait()
        self.source = f"gstreamer {device} {fmt} exited {proc.returncode}: {errors(proc)[-200:]}"
        return count[0]

    # ---- keyframes ------------------------------------------------------
    def ingest(self, jpeg: bytes, at: float | None = None, small: bytes | None = None) -> str | None:
        """Keep a frame when its thumbnail changed or the heartbeat is due; pushed frames (no thumbnail) are always kept."""
        now = at or time.time()
        self.latest, self.latest_at = jpeg, now
        self.seen += 1
        changed = small is None or difference(small, self._last_small) > CHANGE_THRESHOLD
        if changed or now - self._last_kept_at >= HEARTBEAT_S:
            if small is not None:
                self._last_small = small
            self._last_kept_at = now
            self.kept += 1
            frame_id = uuid.uuid4().hex[:8]
            path = self.cfg.frames_dir / f"{frame_id}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(jpeg)
            self.core.emit("frame_kept", frame_id=frame_id, path=str(path), at=now)
            return frame_id
        return None

    def recent(self, seconds: float, limit: int = 6) -> list[dict]:
        cutoff = time.time() - seconds
        frames = [f for f in self.core.state.frames.values() if f["t"] >= cutoff]
        if len(frames) > limit:
            step = len(frames) / float(limit)
            frames = [frames[int(i * step)] for i in range(limit - 1)] + [frames[-1]]
        return frames

    def status(self) -> dict:
        return {"source": self.source, "latest_age_s": round(time.time() - self.latest_at, 1) if self.latest else None,
                "frames_seen": self.seen, "frames_kept": self.kept}

    # ---- the look sense ---------------------------------------------------
    def images(self, frames: list[dict]) -> list[dict]:
        parts, now = [], time.time()
        for f in frames:
            try:
                data = Path(f["path"]).read_bytes()
            except OSError:
                continue
            parts.append(text_part(f"Frame {f['id']} ({now - f['t']:.0f}s ago):"))
            parts.append(image_part(data))
        return parts

    def look(self, question: str, seconds: float = 10) -> dict:
        frames = self.recent(seconds, limit=max(1, min(6, self.cfg.max_images)))
        if not frames:
            return {"answer": "I can't see anything right now.", "yes": None, "frames": 0}
        content = self.images(frames) + [text_part(f"Question about these camera frames (oldest first): {question}\n" + LOOK_REPLY)]
        result, secs = self.core.vision.ask_json([{"role": "user", "content": content}], max_tokens=200, kind="look")
        result["yes"] = as_yes(result.get("yes"))
        result["answer"] = caption(result.get("answer"), result["yes"])
        result["frames"], result["seconds"] = len(frames), round(secs, 2)
        fid = result.get("frame_id") if result.get("frame_id") in [f["id"] for f in frames] else frames[-1]["id"]
        self.core.emit("observed", text=result["answer"], frame_id=fid, source="look")
        return result


LOOK_REPLY = ('Reply only JSON: {"answer": "<short sentence, 5-12 words, saying what you see, e.g. '
              '\'The cable is plugged into port 12.\'>", "yes": true|false|null, "frame_id": "<best frame id or null>"}')
BARE = {"yes", "no", "true", "false", "null", "none", "y", "n", "maybe", "unknown", "can't tell", "cannot tell", "not yet",
        "done", "not done", "yes it is", "no it isn't"}


def as_yes(value):
    if isinstance(value, bool) or value is None:
        return value
    return {"true": True, "yes": True, "false": False, "no": False}.get(str(value).strip().lower())


def caption(answer, yes) -> str:
    """A short sentence for the 'Observed:' line; a bare yes/no/True/False becomes a plain sentence."""
    text = answer.strip() if isinstance(answer, str) else ""
    if len(text.split()) < 2 or text.lower().strip(" .!") in BARE:
        text = {True: "Yes, that looks done.", False: "No, that isn't done yet."}.get(yes, "I can't tell from the camera.")
    return text[0].upper() + text[1:]
