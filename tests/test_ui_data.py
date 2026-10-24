"""The data the round-2 screen reads: hero line and kind, the Observed caption, step keyframes, voice state."""
from __future__ import annotations

import json
import tempfile
import time
import unittest

from journeyman import ops
from journeyman.config import Config
from journeyman.core import Core
from tests import fake_gemma

JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")
LABEL_GAP = {"new_steps": [{"title": "Unplug the old cable", "action": "unplugs the old network cable",
                            "frame_id": "made-up"}],
             "unexplained": [{"action": "labels both ends of the new cable", "confidence": 0.9}]}


class UiDataTest(unittest.TestCase):
    def setUp(self):
        self.server, self.script, url = fake_gemma.start()
        self.cfg = Config(gemma_url=url, data_dir=tempfile.mkdtemp(), camera="none", observe_every_s=0,
                          question_gap_s=20, silence_gate_s=1.5, expert_name="Alex")
        self.core = Core(self.cfg, sync=True)
        self.c = self.core.conductor

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def api_state(self) -> dict:
        return json.loads(self.core.dump())

    def test_capture_hero_observed_and_frames(self):
        self.c.submit_text("start capture")
        snap = self.api_state()
        self.assertEqual(snap["hero"]["kind"], "instructing")
        self.assertIn("Recording", snap["hero"]["text"])
        self.assertIsNone(snap["observed"])
        self.assertIn("voice_state", snap)

        self.script.observe_replies.append(LABEL_GAP)
        fid = self.core.eyes.ingest(JPEG)
        self.c.observe(force=True)
        snap = self.api_state()
        self.assertEqual(snap["observed"]["text"], "unplugs the old network cable; labels both ends of the new cable")
        self.assertEqual(snap["observed"]["frame_id"], fid)
        self.assertEqual(snap["procedures"]["cable-swap"]["steps"][0]["frame_id"], fid)

        self.core._speaking_until = 0
        self.assertTrue(self.c.maybe_ask(time.time() + 30))
        snap = self.api_state()
        self.assertEqual(snap["hero"], {"text": "Why did you label both ends before plugging in?", "kind": "asking",
                                        "t": snap["hero"]["t"]})
        self.assertTrue(all(st["frame_id"] == fid for st in snap["procedures"]["cable-swap"]["steps"]))

        self.c.submit_text("so the next tech can trace it")
        self.assertEqual(self.api_state()["hero"]["kind"], "instructing")
        status = ops.run(self.core, "status")
        self.assertEqual(status["observed"]["frame_id"], fid)
        self.assertEqual(status["hero"]["text"], "Got it, saved.")

    def test_step_frame_falls_back_to_latest_and_keeps_good_ids(self):
        ops.run(self.core, "save_step", {"title": "No camera yet", "action": "does a thing"})
        self.assertNotIn("frame_id", self.core.state.steps["cable-swap/1"])
        first = self.core.eyes.ingest(JPEG, at=time.time() - 10)
        latest = self.core.eyes.ingest(JPEG)
        ops.run(self.core, "save_step", {"title": "Agent step", "action": "presses the latch", "frame_id": "bogus"})
        ops.run(self.core, "save_step", {"title": "Picked frame", "action": "lifts the lid", "frame_id": first})
        steps = self.core.state.procedure_steps("cable-swap")
        self.assertEqual([st.get("frame_id") for st in steps], [None, latest, first])
        rebuilt = Core(self.cfg, sync=True)
        self.assertEqual([st.get("frame_id") for st in rebuilt.state.procedure_steps("cable-swap")],
                         [None, latest, first])
        self.assertEqual(rebuilt.state.hero, self.core.state.hero)

    def test_step_frames_survive_frame_eviction(self):
        fid = self.core.eyes.ingest(JPEG, at=1.0)
        ops.run(self.core, "save_step", {"title": "Keep me", "action": "keeps a frame"})
        for i in range(605):
            self.core.state.apply({"seq": 10_000 + i, "t": 2.0 + i, "type": "frame_kept",
                                   "data": {"frame_id": f"x{i}", "path": "/nope"}})
        self.assertIn(fid, self.core.state.frames)
        self.assertEqual(len(self.core.state.frames), 600)

    def seed(self):
        for title, action, cue in [("Unplug the old cable", "unplugs the old network cable", "port one is empty"),
                                   ("Label both ends", "labels both ends of the new cable", "a tag on each end")]:
            ops.run(self.core, "save_step", {"title": title, "action": action, "cue": cue, "procedure": "cable-swap"})
        ops.run(self.core, "save_why", {"step": "cable-swap/2", "why": "So the next tech can trace it."})

    def test_teach_instructing_correcting_and_look_caption(self):
        self.seed()
        self.c.submit_text("teach me")
        snap = self.api_state()
        self.assertEqual(snap["hero"]["kind"], "instructing")
        self.assertIn("Step 1 of 2", snap["hero"]["text"])
        self.assertIsNone(snap["observed"])

        self.script.look_answers = [True, False]
        fid = self.core.eyes.ingest(JPEG)
        self.c.submit_text("done")
        snap = self.api_state()
        self.assertEqual(snap["hero"]["kind"], "instructing")
        self.assertIn("Step 2 of 2", snap["hero"]["text"])
        self.assertEqual(snap["observed"], {"text": "True", "frame_id": fid, "source": "look", "t": snap["observed"]["t"]})

        self.c.submit_text("done")
        snap = self.api_state()
        self.assertEqual(snap["hero"]["kind"], "correcting")
        self.assertTrue(snap["hero"]["text"].startswith("Hold on"))
        self.assertEqual(snap["observed"]["text"], "False")

        self.c.submit_text("repeat")
        self.assertEqual(self.api_state()["hero"]["kind"], "instructing")

    def test_voice_state_is_passed_through_when_present(self):
        self.assertEqual(self.api_state()["voice_state"], "listening")
        self.core.state.voice_state = "speaking"
        self.assertEqual(self.api_state()["voice_state"], "speaking")
        self.assertEqual(ops.run(self.core, "status")["voice_state"], "speaking")


if __name__ == "__main__":
    unittest.main()
