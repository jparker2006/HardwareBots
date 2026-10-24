"""scripts/thor-up.sh and thor-down.sh: dry runs for each model mix, and a full up/down with a fake docker and fake vLLM."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.join(os.path.dirname(__file__), "..")
UP = os.path.join(ROOT, "scripts", "thor-up.sh")
DOWN = os.path.join(ROOT, "scripts", "thor-down.sh")

FAKE_DOCKER = r"""#!/usr/bin/env bash
echo "$*" >> "$FAKE_STATE/calls"
name="${@: -1}"
case "$1" in
  info) exit 0 ;;
  run) shift; while [ "$1" != "--name" ]; do shift; done; touch "$FAKE_STATE/$2"; echo cid ;;
  inspect) [ -f "$FAKE_STATE/$name" ] || exit 1; [ "$2" = "-f" ] && { [ -n "${FAKE_DEAD:-}" ] && echo false || echo true; } ;;
  stop|rm) rm -f "$FAKE_STATE/$name" ;;
  logs) echo "CUDA out of memory" ;;
esac
exit 0
"""


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def models_server(model_id: str) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            data = json.dumps({"data": [{"id": model_id}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@unittest.skipUnless(shutil.which("bash") and shutil.which("curl"), "needs bash and curl")
class ThorScriptsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir31 = os.path.join(self.tmp, "Gemma-4-31B-IT-NVFP4")
        os.makedirs(self.dir31)
        self.state = os.path.join(self.tmp, "docker")
        os.makedirs(self.state)
        docker = os.path.join(self.tmp, "fake-docker")
        with open(docker, "w") as f:
            f.write(FAKE_DOCKER)
        os.chmod(docker, 0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("JM_", "HF_"))}
        self.env.update(JM_31B_DIR=self.dir31, JM_HF_CACHE=os.path.join(self.tmp, "hf"), JM_DOCKER=docker,
                        JM_RUN_DIR=os.path.join(self.tmp, "run"), FAKE_STATE=self.state)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sh(self, script, *args, **env):
        p = subprocess.run(["bash", script, *args], env={**self.env, **env}, capture_output=True, text=True,
                           timeout=60)
        return p.returncode, p.stdout + p.stderr

    def test_dry_run_both(self):
        code, out = self.sh(UP, "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("vllm serve /models/Gemma-4-31B-IT-NVFP4 --served-model-name gemma-4-31b", out)
        self.assertIn("vllm serve google/gemma-4-E4B-it --served-model-name gemma-4-e4b", out)
        self.assertIn("--enable-auto-tool-choice --tool-call-parser gemma4", out)
        self.assertIn("not cached; vLLM will download it", out)
        self.assertIn("JM_GEMMA_URL=http://127.0.0.1:8000/v1 JM_GEMMA_MODEL=gemma-4-e4b", out)
        self.assertIn("JM_VISION_URL=http://127.0.0.1:8001/v1 JM_VISION_MODEL=gemma-4-31b", out)
        self.assertIn("jm doctor", out)
        self.assertIn("jm serve", out)
        self.assertIn("dry run done", out)
        self.assertFalse(os.path.exists(os.path.join(self.state, "calls")), "dry run must not call docker")

    def test_dry_run_only_31b(self):
        code, out = self.sh(UP, "--dry-run", "--only-31b")
        self.assertEqual(code, 0, out)
        self.assertNotIn("gemma-4-E4B-it", out)
        self.assertIn("JM_GEMMA_URL=http://127.0.0.1:8001/v1 JM_GEMMA_MODEL=gemma-4-31b", out)
        self.assertIn("no audio input", out)

    def test_dry_run_only_e4b_cached_runs_offline(self):
        snap = os.path.join(self.tmp, "hf", "hub", "models--google--gemma-4-E4B-it", "snapshots", "abc")
        os.makedirs(snap)
        open(os.path.join(snap, "config.json"), "w").close()
        code, out = self.sh(UP, "--dry-run", "--only-e4b")
        self.assertEqual(code, 0, out)
        self.assertNotIn("31B", out)
        self.assertIn("-e HF_HUB_OFFLINE=1", out)
        self.assertIn("JM_VISION_URL=(same)", out)

    def test_missing_31b_falls_back_to_e4b(self):
        code, out = self.sh(UP, "--dry-run", JM_31B_DIR=os.path.join(self.tmp, "nope"))
        self.assertEqual(code, 0, out)
        self.assertIn("skip  31B", out)
        self.assertIn("JM_GEMMA_URL=http://127.0.0.1:8000/v1", out)
        code, out = self.sh(UP, "--dry-run", "--only-31b", JM_31B_DIR=os.path.join(self.tmp, "nope"))
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL  no Gemma server is up", out)

    def test_dead_container_fails_with_log(self):
        code, out = self.sh(UP, "--only-31b", "--no-serve", "--no-doctor", JM_31B_PORT=str(free_port()),
                            JM_UP_TIMEOUT="10", FAKE_DEAD="1")
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL  jm-gemma-31b exited before answering", out)
        self.assertIn("CUDA out of memory", out)

    def test_up_then_down(self):
        s31, se4 = models_server("gemma-4-31b"), models_server("gemma-4-e4b")
        for s in (s31, se4):
            self.addCleanup(s.server_close)
            self.addCleanup(s.shutdown)
        jm_port = str(free_port())
        env = dict(JM_31B_PORT=str(s31.server_address[1]), JM_E4B_PORT=str(se4.server_address[1]),
                   JM_PORT=jm_port, JM_CAMERA="none", JM_DATA=os.path.join(self.tmp, "data"), JM_RECORD="0")
        code, out = self.sh(UP, "--no-doctor", **env)
        self.addCleanup(self.sh, DOWN)
        self.assertEqual(code, 0, out)
        self.assertIn("PASS  jm-gemma-31b answers", out)
        self.assertIn("PASS  jm-gemma-e4b answers", out)
        self.assertIn(f"PASS  jm serve on http://127.0.0.1:{jm_port}", out)
        self.assertIn("PASS  Thor is up", out)
        with open(os.path.join(self.tmp, "run", "env.sh")) as f:
            saved = f.read()
        self.assertIn(f"JM_VISION_URL=http://127.0.0.1:{s31.server_address[1]}/v1", saved)
        self.assertEqual(sorted(os.listdir(self.state)), ["calls", "jm-gemma-31b", "jm-gemma-e4b"])

        code, out = self.sh(UP, "--no-doctor", "--no-serve", **env)
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("already running; reusing it"), 2, out)

        with open(os.path.join(self.tmp, "run", "serve.pid")) as f:
            pid = int(f.read())
        code, out = self.sh(DOWN)
        self.assertEqual(code, 0, out)
        self.assertIn("stopped jm serve", out)
        self.assertIn("stopped jm-gemma-31b", out)
        self.assertEqual(os.listdir(self.state), ["calls"])
        with self.assertRaises(OSError):
            os.kill(pid, 0)
        code, out = self.sh(DOWN)
        self.assertEqual(code, 0, out)
        self.assertNotIn("stopped", out)


if __name__ == "__main__":
    unittest.main()
