"""scripts/mac-up.sh and scripts/mac_camera.py: dry run, cloud-tag refusal, a full up/stop against a fake Ollama."""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scripts import mac_camera
from tests.test_thor_scripts import free_port

ROOT = os.path.join(os.path.dirname(__file__), "..")
UP = os.path.join(ROOT, "scripts", "mac-up.sh")

FFMPEG_LIST = """[AVFoundation indev @ 0x1] AVFoundation video devices:
[AVFoundation indev @ 0x1] [0] FaceTime HD Camera
[AVFoundation indev @ 0x1] [1] Logitech BRIO
[AVFoundation indev @ 0x1] [2] Capture screen 0
[AVFoundation indev @ 0x1] AVFoundation audio devices:
[AVFoundation indev @ 0x1] [0] MacBook Air Microphone
"""


def ollama_server(models) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, obj):
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.reply({"models": [{"name": m, "model": m} for m in models]})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.reply({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class MacCameraTest(unittest.TestCase):
    def test_parse_and_pick(self):
        cams = mac_camera.parse_devices(FFMPEG_LIST)
        self.assertEqual(cams, [("0", "FaceTime HD Camera"), ("1", "Logitech BRIO")])
        self.assertEqual(mac_camera.pick(cams, "auto"), ("1", "Logitech BRIO"))
        self.assertEqual(mac_camera.pick(cams[:1], "auto"), ("0", "FaceTime HD Camera"))
        self.assertEqual(mac_camera.pick(cams, "facetime")[0], "0")
        self.assertEqual(mac_camera.pick(cams, "0")[0], "0")
        self.assertIsNone(mac_camera.pick(cams, "nope"))
        self.assertIsNone(mac_camera.pick([], "auto"))

    def test_command(self):
        cmd = mac_camera.command("1", 1.0, 512, ["-framerate", "30"])
        self.assertEqual(cmd[cmd.index("-f") + 1], "avfoundation")
        self.assertIn("1:none", cmd)
        self.assertIn("fps=1,scale=512:-2", cmd)
        self.assertEqual(cmd[-4:], ["image2pipe", "-c:v", "mjpeg", "-"])

    def test_run_pushes_each_jpeg(self):
        got = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        script = "import sys; sys.stdout.buffer.write(b'\\xff\\xd8one\\xff\\xd9\\xff\\xd8two\\xff\\xd9\\xff\\xd8par')"
        sent = mac_camera.run([sys.executable, "-c", script], f"http://127.0.0.1:{server.server_address[1]}")
        self.assertEqual(sent, 2)
        self.assertEqual([base64.b64decode(g["jpeg_b64"]) for g in got], [b"\xff\xd8one\xff\xd9", b"\xff\xd8two\xff\xd9"])


@unittest.skipUnless(shutil.which("bash") and shutil.which("curl"), "needs bash and curl")
class MacUpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(bin_dir)
        fakes = {"ollama": 'echo "$*" >> "%s/ollama.calls"\n' % self.tmp,
                 "ffmpeg": "cat >&2 <<'X'\n%sX\nexit 1\n" % FFMPEG_LIST}
        for name, body in fakes.items():
            path = os.path.join(bin_dir, name)
            with open(path, "w") as f:
                f.write("#!/usr/bin/env bash\n" + body)
            os.chmod(path, 0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("JM_")}
        self.env.update(PATH=bin_dir + os.pathsep + self.env.get("PATH", ""), JM_RUN_DIR=os.path.join(self.tmp, "run"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sh(self, *args, **env):
        p = subprocess.run(["bash", UP, *args], env={**self.env, **env}, capture_output=True, text=True, timeout=90)
        return p.returncode, p.stdout + p.stderr

    def fake(self, *models):
        s = ollama_server(models)
        self.addCleanup(s.server_close)
        self.addCleanup(s.shutdown)
        return f"http://127.0.0.1:{s.server_address[1]}"

    def test_dry_run(self):
        code, out = self.sh("--dry-run", JM_OLLAMA_URL=self.fake("gemma4:e4b"))
        self.assertEqual(code, 0, out)
        self.assertIn("PASS  Ollama answers", out)
        self.assertIn("PASS  model gemma4:e4b is on this Mac", out)
        self.assertIn("ollama create jm-gemma4-e4b", out)
        self.assertIn("camera via ffmpeg avfoundation", out)
        self.assertIn("1  Logitech BRIO", out)
        self.assertIn("JM_GEMMA_MODEL=jm-gemma4-e4b JM_CAMERA=off", out)
        self.assertIn("scripts/mac_camera.py --jm http://127.0.0.1:3000", out)
        self.assertIn("headset/headset.py --thor http://127.0.0.1:3000 --text", out)
        self.assertIn("dry run done", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "ollama.calls")), "dry run must not call ollama")

    def test_refuses_cloud_and_remote(self):
        code, out = self.sh("--dry-run", JM_MAC_MODEL="gemma4:31b-cloud")
        self.assertEqual(code, 1, out)
        self.assertIn("cloud model", out)
        code, out = self.sh("--dry-run", JM_OLLAMA_URL="http://ollama.example.com:11434")
        self.assertEqual(code, 1, out)
        self.assertIn("is not this Mac", out)

    def test_missing_model_no_pull(self):
        code, out = self.sh("--no-pull", JM_OLLAMA_URL=self.fake("llama3:8b"))
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL  model gemma4:e4b missing", out)

    def test_up_then_stop(self):
        port = str(free_port())
        code, out = self.sh("--no-doctor", JM_OLLAMA_URL=self.fake("gemma4:e4b"), JM_PORT=port,
                            JM_MAC_CAMERA_VIA="none", JM_DATA=os.path.join(self.tmp, "data"), JM_RECORD="0")
        self.addCleanup(self.sh, "--stop")
        self.assertEqual(code, 0, out)
        self.assertIn("PASS  jm-gemma4-e4b = gemma4:e4b with num_ctx 8192", out)
        self.assertIn("PASS  jm-gemma4-e4b answers", out)
        self.assertIn(f"PASS  jm serve on http://127.0.0.1:{port}", out)
        self.assertIn("PASS  Mac is up", out)
        with open(os.path.join(self.tmp, "run", "env.sh")) as f:
            self.assertIn("JM_GEMMA_MODEL=jm-gemma4-e4b", f.read())
        with open(os.path.join(self.tmp, "run", "Modelfile")) as f:
            self.assertEqual(f.read(), "FROM gemma4:e4b\nPARAMETER num_ctx 8192\n")
        code, out = self.sh("--stop")
        self.assertIn("stopped serve", out)


if __name__ == "__main__":
    unittest.main()
