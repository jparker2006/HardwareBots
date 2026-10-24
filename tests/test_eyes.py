"""Camera path without OpenCV: device pick, GStreamer pipeline, JPEG stream split, keyframes, `jm camera-test`."""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from journeyman import eyes  # noqa: E402
from journeyman.config import Config  # noqa: E402
from journeyman.core import Core  # noqa: E402

JPEG = bytes.fromhex("ffd8ffc0001108012002000301220002110103110100ffd9")
FAKE_GST = '''#!{python}
import os, sys
fds = [int(a[3:]) for a in sys.argv if a.startswith("fd=")]
if "-v" in sys.argv:
    print("/GstPipeline:pipeline0/GstV4l2Src:v4l2src0.GstPad:src: caps = image/jpeg, width=(int)1280, "
          "height=(int)720, framerate=(fraction)30/1", flush=True)
for i in range(4):
    if len(fds) > 1:
        os.write(fds[1], bytes([0 if i < 2 else 200]) * 576)
    os.write(fds[0], bytes.fromhex("{jpeg}")[:7])
    os.write(fds[0], bytes.fromhex("{jpeg}")[7:])
sys.stderr.write("done\\n")
'''


def brio(device):
    nodes = {"/dev/video0": {"card": "Logitech BRIO", "capture": True, "meta": False, "formats": ["YUYV", "MJPG"]},
             "/dev/video1": {"card": "Logitech BRIO", "capture": False, "meta": True, "formats": []},
             "/dev/video2": {"card": "Other cam", "capture": True, "meta": False, "formats": ["YUYV"]}}
    if device not in nodes:
        return {"device": device, "capture": None, "formats": [], "error": "No such file or directory"}
    return dict(nodes[device], device=device)


class PipelineTest(unittest.TestCase):
    def test_mjpeg_pipeline(self):
        cmd = eyes.pipeline("/dev/video0", "mjpeg", width=512, fps=1.0, fds=(7, 9))
        line = " ".join(cmd)
        self.assertEqual(cmd[:4], ["gst-launch-1.0", "-q", "v4l2src", "device=/dev/video0"])
        self.assertIn("image/jpeg,width=1280,height=720 ! jpegdec ! videoconvert", line)
        self.assertIn("video/x-raw,width=512,pixel-aspect-ratio=1/1", line)
        self.assertIn("videorate drop-only=true ! video/x-raw,framerate=1/1 ! tee name=t", line)
        self.assertIn("jpegenc quality=80 ! fdsink fd=7", line)
        self.assertIn("video/x-raw,format=GRAY8,width=32,height=18 ! fdsink fd=9", line)

    def test_raw_fallback_and_snapshot_pipeline(self):
        line = " ".join(eyes.pipeline("/dev/video0", "raw", width=None, fps=None, thumbs=False, num_buffers=30,
                                      verbose=True))
        self.assertIn("-v v4l2src device=/dev/video0 num-buffers=30 ! video/x-raw ! videoconvert ! jpegenc", line)
        for absent in ("jpegdec", "videoscale", "videorate", "tee", "GRAY8"):
            self.assertNotIn(absent, line)
        self.assertIn("framerate=1/2", " ".join(eyes.pipeline("/dev/video0", fps=0.5)))

    def test_formats_to_try(self):
        self.assertEqual(eyes.formats_to_try(["YUYV", "MJPG"]), ["mjpeg", "raw"])
        self.assertEqual(eyes.formats_to_try(["YUYV"]), ["raw"])
        self.assertEqual(eyes.formats_to_try([]), ["mjpeg", "raw"])

    def test_pick_capture_node_not_metadata(self):
        devices = ["/dev/video0", "/dev/video1", "/dev/video2"]
        self.assertEqual(eyes.pick_device("0", devices, brio)["device"], "/dev/video0")
        self.assertEqual(eyes.pick_device("1", devices, brio)["device"], "/dev/video0")
        self.assertEqual(eyes.pick_device("/dev/video1", devices, brio)["card"], "Logitech BRIO")
        self.assertEqual(eyes.pick_device("auto", devices, brio)["device"], "/dev/video0")
        self.assertEqual(eyes.pick_device("2", devices, brio)["device"], "/dev/video2")
        self.assertEqual(eyes.pick_device("5", devices, brio)["device"], "/dev/video0")
        gone = eyes.pick_device("0", [], lambda d: {"device": d, "capture": None, "formats": [], "error": "missing"})
        self.assertEqual((gone["device"], "error" in gone), ("/dev/video0", True))

    def test_probe_non_camera(self):
        self.assertIn("error", eyes.probe("/dev/null"))
        self.assertIn("error", eyes.probe("/dev/video-missing"))

    def test_split_jpegs_across_reads(self):
        buf = bytearray(b"junk" + JPEG[:5])
        self.assertEqual(eyes.split_jpegs(buf), [])
        buf.extend(JPEG[5:] + JPEG + JPEG[:3])
        self.assertEqual(eyes.split_jpegs(buf), [JPEG, JPEG])
        self.assertEqual(bytes(buf), JPEG[:3])

    def test_jpeg_size_and_difference(self):
        self.assertEqual(eyes.jpeg_size(JPEG), (512, 288))
        self.assertIsNone(eyes.jpeg_size(b"\xff\xd8nope"))
        self.assertEqual(eyes.difference(bytes(576), bytes(576)), 0.0)
        self.assertEqual(eyes.difference(bytes(576), bytes([10]) * 576), 10.0)
        self.assertEqual(eyes.difference(None, bytes(576)), 255.0)


class KeyframeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.core = Core(Config(gemma_url="http://127.0.0.1:9/v1", data_dir=self.tmp, camera="none"), sync=True)
        self.eyes = self.core.eyes

    def test_keep_on_change_plus_heartbeat(self):
        still, moved, t = bytes([50]) * 576, bytes([50]) * 500 + bytes([250]) * 76, 1000.0
        kept = [self.eyes.ingest(JPEG, at=t + i, small=still) is not None for i in range(7)]
        self.assertEqual(kept, [True, False, False, False, False, True, False])
        self.assertIsNotNone(self.eyes.ingest(JPEG, at=t + 7, small=moved))
        self.assertIsNone(self.eyes.ingest(JPEG, at=t + 8, small=moved))
        self.assertIsNotNone(self.eyes.ingest(JPEG, at=t + 9, small=still))
        self.assertEqual(self.eyes.status()["frames_seen"], 10)
        self.assertEqual(self.eyes.status()["frames_kept"], 4)
        self.assertEqual(len(self.core.state.frames), 4)

    def test_pushed_frames_without_thumbnail_are_kept(self):
        self.assertIsNotNone(self.eyes.ingest(JPEG))
        self.assertIsNotNone(self.eyes.ingest(JPEG))


class FakeGstTest(unittest.TestCase):
    def setUp(self):
        self.bin = tempfile.mkdtemp()
        gst = Path(self.bin) / "gst-launch-1.0"
        gst.write_text(FAKE_GST.format(python=sys.executable, jpeg=JPEG.hex()))
        gst.chmod(gst.stat().st_mode | stat.S_IEXEC)
        self.env = mock.patch.dict(os.environ, {"PATH": self.bin + os.pathsep + os.environ["PATH"]})
        self.env.start()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        self.env.stop()

    def test_camera_test_saves_jpeg_and_reports(self):
        with mock.patch.object(eyes, "probe", brio), \
                mock.patch.object(eyes, "list_devices", lambda: ["/dev/video0", "/dev/video1"]):
            r = eyes.camera_test("1", Path(self.tmp) / "snap.jpg", frames=4)
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["device"], r["card"], r["format"], r["frames"]), ("/dev/video0", "Logitech BRIO", "mjpeg", 4))
        self.assertEqual(r["fps_negotiated"], 30.0)
        self.assertEqual(r["snapshot_size"], (512, 288))
        self.assertEqual((Path(self.tmp) / "snap.jpg").read_bytes(), JPEG)

    def test_gst_capture_keeps_changed_frames(self):
        core = Core(Config(gemma_url="http://127.0.0.1:9/v1", data_dir=self.tmp, camera="none"), sync=True)
        n = core.eyes._run_gst({"device": "/dev/video0", "card": "Logitech BRIO"}, "mjpeg")
        self.assertEqual(n, 4)
        self.assertEqual(core.eyes.latest, JPEG)
        self.assertGreaterEqual(core.eyes.kept, 1)
        self.assertLess(core.eyes.kept, 4 + 1)
        self.assertIn("exited 0", core.eyes.source)
        self.assertLess(time.time() - core.eyes.latest_at, 30)


if __name__ == "__main__":
    unittest.main()
