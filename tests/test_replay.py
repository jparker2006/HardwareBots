"""Record a live session in-process against the fake Gemma, then replay it into a fresh Core."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from journeyman import cli  # noqa: E402
from journeyman.config import Config  # noqa: E402
from journeyman.core import Core  # noqa: E402
from journeyman.recorder import Recorder, load_script, replay_into, summarize  # noqa: E402
from journeyman.server import make_handler  # noqa: E402
from tests import fake_gemma  # noqa: E402

JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + bytes(28)


def make_core():
    server, _script, url = fake_gemma.start()
    cfg = Config(gemma_url=url, data_dir=tempfile.mkdtemp(), camera="none", observe_every_s=0,
                 question_gap_s=0, silence_gate_s=0, expert_name="Alex")
    return server, Core(cfg, sync=True)


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            s.shutdown()

    def core(self):
        server, core = make_core()
        self.servers.append(server)
        return core

    def summary(self, core):
        return ([st["title"] for st in core.state.procedure_steps("cable-swap")], len(core.state.questions),
                [w["why"] for st in core.state.procedure_steps("cable-swap") for w in st["whys"]])

    def test_record_then_replay(self):
        live = self.core()
        Recorder(live)
        c = live.conductor
        live.conductor.submit_text("idle chatter before any session")
        for act in (lambda: c.submit_audio(WAV, 0, 0), lambda: live.eyes.ingest(JPEG),
                    lambda: c.submit_text("First I pull the old cable out of port one"),
                    lambda: c.submit_text("So the next tech can trace it without pulling every cable"),
                    lambda: c.submit_text("Okay I'm done")):
            act()
            c.tick()
        live.eyes.ingest(JPEG)
        expected = self.summary(live)
        self.assertTrue(expected[0])
        self.assertEqual(live.state.mode, "idle")

        rec = live.config.data_dir / "recordings" / next(iter(live.state.sessions))
        lines = load_script(rec)
        self.assertEqual(lines[0]["at"], 0)
        self.assertEqual(lines[0]["heard"], "start capture")
        self.assertTrue((rec / lines[0]["wav"]).read_bytes() == WAV)
        self.assertEqual([x["text"] for x in lines if "text" in x][-1], "Okay I'm done")
        self.assertFalse(any("op" in x for x in lines))
        self.assertTrue(all(sum(k in x for k in ("text", "wav", "frame", "op")) == 1 for x in lines))
        self.assertEqual(len([x for x in lines if "frame" in x]), 1)
        s = summarize(rec)
        self.assertEqual((s["utterances"], s["audio"], s["frames"], s["kind"]), (4, 1, 1, "capture"))

        fresh = self.core()
        replay_into(fresh, rec)
        self.assertEqual(self.summary(fresh), expected)
        self.assertEqual(fresh.state.mode, "idle")

    def test_ops_started_session_records_ops(self):
        live = self.core()
        Recorder(live)
        live.conductor.start_session("capture", procedure="cable-swap")
        live.conductor.submit_text("Now I pull the old cable straight out")
        live.conductor.end_session()
        rec = live.config.data_dir / "recordings" / next(iter(live.state.sessions))
        lines = load_script(rec)
        self.assertEqual(lines[0]["op"], "session_start")
        self.assertEqual(lines[0]["args"], {"kind": "capture", "procedure": "cable-swap"})
        self.assertEqual(lines[-1]["op"], "session_end")
        meta = json.loads((rec / "meta.json").read_text())
        self.assertIsNotNone(meta["ended"])

        fresh = self.core()
        replay_into(fresh, rec)
        self.assertEqual(self.summary(fresh), self.summary(live))

    def test_demo_script_replays_and_lists(self):
        demo = Path(__file__).parent.parent / "recordings" / "demo"
        fresh = self.core()
        self.assertEqual(replay_into(fresh, demo), 8)
        self.assertIn("teach", [x["kind"] for x in fresh.state.sessions.values()])
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["recordings", str(demo.parent)])
        self.assertIn("8 utterances", out.getvalue())

    def test_frames_recording_saves_steps_with_photos(self):
        rec = Path(__file__).parent.parent / "recordings" / "demo-frames"
        fresh = self.core()
        self.assertEqual(replay_into(fresh, rec), 13)
        steps = fresh.state.procedure_steps("cable-swap")
        self.assertTrue(steps)
        self.assertTrue(all(st.get("frame_id") in fresh.state.frames for st in steps))
        self.assertTrue(fresh.state.gaps)
        self.assertEqual(summarize(rec)["frames"], 7)

    def test_jm_replay_fast_posts_frames_to_the_service(self):
        core = self.core()
        ui = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(core))
        threading.Thread(target=ui.serve_forever, daemon=True).start()
        self.addCleanup(ui.server_close)
        self.addCleanup(ui.shutdown)
        url = f"http://127.0.0.1:{ui.server_port}"
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(["--url", url, "replay", str(Path(__file__).parent.parent / "recordings" / "demo-frames"),
                      "--fast", "--settle", "1"])
        self.assertIn("replayed 13 lines", out.getvalue())
        steps = core.state.procedure_steps("cable-swap")
        self.assertTrue(steps)
        fid = steps[0]["frame_id"]
        with urllib.request.urlopen(f"{url}/api/frames/{fid}.jpg") as r:
            self.assertEqual(r.read()[:2], b"\xff\xd8")


if __name__ == "__main__":
    unittest.main()
