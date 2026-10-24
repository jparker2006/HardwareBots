"""Camera capture, keyframe selection and the `look` sense."""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .gemma import image_part, text_part

try:
    import cv2  # type: ignore
except Exception:  # OpenCV is optional; GStreamer or pushed frames work without it
    cv2 = None


class Eyes:
    def __init__(self, core):
        self.core = core
        self.cfg = core.config
        self.latest: bytes | None = None
        self.latest_at = 0.0
        self.source = "none"
        self._last_small = None
        self._last_kept_at = 0.0
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
                    small = cv2.cvtColor(cv2.resize(frame, (64, 36)), cv2.COLOR_BGR2GRAY)
                    self.ingest(buf.tobytes(), small=small)
            time.sleep(self.cfg.frame_interval_s)

    def _gst_loop(self, cam: str) -> None:
        out = self.cfg.data_dir / "gst"
        out.mkdir(parents=True, exist_ok=True)
        device = f"/dev/video{cam}" if cam.isdigit() else cam
        rate = max(1, int(round(1 / self.cfg.frame_interval_s)))
        cmd = ["gst-launch-1.0", "-q", "v4l2src", f"device={device}", "!", "videoconvert", "!", "videoscale", "!",
               f"video/x-raw,width={self.cfg.frame_width},height={int(self.cfg.frame_width * 9 / 16)}", "!",
               "videorate", "!", f"video/x-raw,framerate={rate}/1", "!", "jpegenc", "!",
               "multifilesink", f"location={out}/%06d.jpg", "max-files=5"]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.source = f"gstreamer {device}"
        seen = None
        while not self._stop.is_set() and proc.poll() is None:
            files = sorted(out.glob("*.jpg"))
            if len(files) >= 2 and files[-2].name != seen:
                seen = files[-2].name
                try:
                    self.ingest(files[-2].read_bytes())
                except OSError:
                    pass
            time.sleep(0.2)
        if proc.poll() is not None:
            self.source = f"gstreamer exited: {(proc.stderr.read() or b'').decode()[:200]}"

    # ---- keyframes ------------------------------------------------------
    def ingest(self, jpeg: bytes, at: float | None = None, small=None) -> str | None:
        now = at or time.time()
        self.latest, self.latest_at = jpeg, now
        changed = True
        if small is not None and self._last_small is not None:
            changed = float(cv2.absdiff(small, self._last_small).mean()) > 4.0
        if changed or now - self._last_kept_at >= 5.0:
            if small is not None:
                self._last_small = small
            self._last_kept_at = now
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
        return {"source": self.source, "latest_age_s": round(time.time() - self.latest_at, 1) if self.latest else None}

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
        frames = self.recent(seconds)
        if not frames:
            return {"answer": "I can't see anything right now.", "yes": None, "frames": 0}
        content = self.images(frames) + [text_part(
            f"Question about these camera frames (oldest first): {question}\n"
            'Reply only JSON: {"answer": "<one sentence>", "yes": true|false|null, "frame_id": "<best frame id or null>"}')]
        result, secs = self.core.vision.ask_json([{"role": "user", "content": content}], max_tokens=200, kind="look")
        result.setdefault("answer", "")
        result["frames"], result["seconds"] = len(frames), round(secs, 2)
        return result
