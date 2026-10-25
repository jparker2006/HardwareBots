"""`jm doctor` and `jm bench` against the scripted Gemma."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from journeyman import cli, doctor  # noqa: E402
from journeyman.config import Config  # noqa: E402
from tests import fake_gemma  # noqa: E402

DEAD = "http://127.0.0.1:9/v1"


class DoctorTest(unittest.TestCase):
    def setUp(self):
        self.server, self.script, self.url = fake_gemma.start()
        self.cfg = Config(gemma_url=self.url, data_dir=tempfile.mkdtemp(), camera="none")

    def tearDown(self):
        self.server.shutdown()

    def run_doctor(self, **kw):
        kw.setdefault("env_url", self.url)
        kw.setdefault("probes", [])
        kw.setdefault("online", False)
        return {c.name: c for c in doctor.run(self.cfg, "http://127.0.0.1:9", **kw)}

    def test_all_gemma_checks_pass(self):
        checks = self.run_doctor()
        for name in ("python", "gemma server", "text round trip", "image input", "audio input", "tool calling",
                     "internet", "disk"):
            self.assertEqual(checks[name].status, "PASS", (name, checks[name]))
        self.assertIn("fake-gemma", checks["gemma server"].detail)
        self.assertEqual(checks["camera"].status, "WARN")
        self.assertEqual(checks["journeyman service"].status, "WARN")
        self.assertFalse(doctor.failed(list(checks.values())))

    def test_audio_dropped_by_server_fails(self):
        real = self.script.reply
        self.script.reply = lambda b: ({"content": "I need an audio file to describe it for you."}
                                       if "input_audio" in json.dumps(b) else real(b))
        checks = self.run_doctor()
        self.assertEqual(checks["audio input"].status, "FAIL")
        self.assertIn("Ollama", checks["audio input"].hint)

    def test_falls_back_to_probing_ports(self):
        self.cfg.gemma_url = DEAD
        checks = self.run_doctor(env_url=DEAD, probes=[(DEAD, "vLLM"), (self.url, "llama.cpp")])
        self.assertEqual(checks["gemma server"].status, "WARN")
        self.assertIn("llama.cpp", checks["gemma server"].detail)
        self.assertIn(f"export JM_GEMMA_URL={self.url}", checks["gemma server"].hint)
        self.assertEqual(checks["text round trip"].status, "PASS")

    def test_no_server_fails(self):
        checks = self.run_doctor(env_url=DEAD, probes=[(DEAD, "vLLM")])
        self.assertEqual(checks["gemma server"].status, "FAIL")
        self.assertNotIn("text round trip", checks)
        self.assertTrue(doctor.failed(list(checks.values())))

    def test_rejected_audio_and_text_tools(self):
        self.script.reject_audio = True
        self.script.text_tools = True
        checks = self.run_doctor()
        self.assertEqual(checks["audio input"].status, "FAIL")
        self.assertIn("E2B/E4B", checks["audio input"].hint)
        self.assertEqual(checks["tool calling"].status, "WARN")
        self.assertIn("--tool-call-parser gemma4", checks["tool calling"].hint)
        self.assertIn("--jinja", checks["tool calling"].hint)
        self.assertEqual(checks["image input"].status, "PASS")

    def test_offline_flag_fails_when_online(self):
        self.assertEqual(self.run_doctor(online=True)["internet"].status, "WARN")
        self.assertEqual(self.run_doctor(online=True, offline=True)["internet"].status, "FAIL")

    def test_builtin_media_is_valid(self):
        self.assertTrue(doctor.RED_JPEG.startswith(b"\xff\xd8") and doctor.RED_JPEG.endswith(b"\xff\xd9"))
        with wave.open(io.BytesIO(doctor.tone_wav())) as w:
            self.assertEqual((w.getframerate(), w.getnframes(), w.getnchannels()), (16000, 16000, 1))

    def test_cli_json_and_exit_code(self):
        env = {"JM_GEMMA_URL": self.url, "JM_CAMERA": "none", "JM_DATA": str(self.cfg.data_dir)}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), mock.patch.object(doctor, "internet_reachable", return_value=False), \
                redirect_stdout(out), self.assertRaises(SystemExit) as exit_:
            cli.main(["--url", "http://127.0.0.1:9", "doctor", "--json"])
        self.assertEqual(exit_.exception.code, 0)
        report = json.loads(out.getvalue())
        self.assertTrue(report["ok"])
        self.assertIn("audio input", [c["name"] for c in report["checks"]])

        self.script.reject_images = True
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), mock.patch.object(doctor, "internet_reachable", return_value=False), \
                redirect_stdout(out), self.assertRaises(SystemExit) as exit_:
            cli.main(["--url", "http://127.0.0.1:9", "doctor"])
        self.assertEqual(exit_.exception.code, 1)
        self.assertIn("FAIL  image input", out.getvalue())

    def test_bench_runs_each_probe(self):
        self.script.reject_audio = True
        results = {r["probe"]: r for r in doctor.bench(self.url, "fake-gemma", runs=3)}
        self.assertEqual(results["text"]["runs"], 3)
        self.assertLessEqual(results["image"]["min_s"], results["image"]["median_s"])
        self.assertEqual(results["tools"]["reply"], "tool_calls")
        self.assertEqual(results["audio"]["runs"], 0)
        self.assertIn("400", results["audio"]["error"])
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"JM_GEMMA_URL": self.url}), redirect_stdout(out):
            cli.main(["bench", "--runs", "2"])
        self.assertIn("median", out.getvalue())
        self.assertIn("audio  ERROR", out.getvalue())


if __name__ == "__main__":
    unittest.main()
