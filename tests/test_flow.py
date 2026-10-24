"""End-to-end capture → teach → improve loop against a scripted Gemma. Run: python3 -m unittest discover tests"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from journeyman import ops  # noqa: E402
from journeyman.config import Config  # noqa: E402
from journeyman.core import Core  # noqa: E402
from journeyman.local_http import NotLocal, check_url  # noqa: E402
from tests import fake_gemma  # noqa: E402

JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.server, self.script, url = fake_gemma.start()
        self.tmp = tempfile.mkdtemp()
        cfg = Config(gemma_url=url, data_dir=self.tmp, camera="none", observe_every_s=0, question_gap_s=0,
                     silence_gate_s=0, expert_name="Alex")
        self.core = Core(cfg, sync=True)

    def tearDown(self):
        self.server.shutdown()

    def types(self):
        return [e["type"] for e in self.core.journal.read()]

    def test_capture_teach_improve(self):
        c, conductor = self.core, self.core.conductor
        conductor.submit_text("Journeyman, start capture")
        self.assertEqual(c.state.mode, "capture")
        c.eyes.ingest(JPEG)
        conductor.submit_text("First I pull the old cable out of port one")
        conductor.tick(now=time.time() + 10)
        self.assertEqual(len(c.state.procedure_steps("cable-swap")), 2)
        self.assertIsNotNone(c.state.pending_question)
        self.assertIn("label", c.state.pending_question["text"].lower())

        conductor.submit_text("So the next tech can trace it without pulling every cable")
        self.assertIsNone(c.state.pending_question)
        step = c.state.steps[c.state.questions[next(iter(c.state.questions))]["step_id"]]
        self.assertEqual(step["whys"][0]["source"], "expert")
        self.assertIn("trace", step["whys"][0]["why"])

        conductor.submit_text("Okay I'm done")
        self.assertEqual(c.state.mode, "idle")
        card = ops.run(c, "card", {"procedure": "cable-swap"})
        self.assertIn("Why:", card)
        self.assertTrue((c.config.cards_dir / "cable-swap.md").exists())

        conductor.submit_text("teach me")
        self.assertEqual(c.state.mode, "teach")
        c.eyes.ingest(JPEG)
        conductor.submit_text("next")
        self.assertEqual(c.state.teach_index, 0)
        self.assertIn("trainee_mistake", self.types())
        conductor.submit_text("done")
        self.assertEqual(c.state.teach_index, 1)

        conductor.submit_text("Why do I label both ends?")
        self.assertIn("trace", c.state.said[-1]["text"])
        conductor.submit_text("What torque should the screws be?")
        self.assertEqual(len(c.state.open_questions), 1)
        self.assertIn("not captured", c.state.said[-1]["text"].lower())

        steps = c.state.procedure_steps("cable-swap")
        self.assertTrue(any(st["warnings"] for st in steps))
        self.assertIn("Common mistake", ops.run(c, "card", {"procedure": "cable-swap"}))

    def test_state_rebuilds_from_journal(self):
        self.core.conductor.submit_text("start capture")
        ops.run(self.core, "save_step", {"title": "Label both ends", "action": "labels the cable"})
        again = Core(self.core.config, sync=True)
        self.assertEqual(again.state.procedure_steps("cable-swap")[0]["title"], "Label both ends")

    def test_agent_cannot_confirm_and_revisions_are_checked(self):
        self.core.conductor.submit_text("start capture")
        ops.run(self.core, "save_step", {"title": "Label both ends", "action": "labels the cable"})
        with self.assertRaises(PermissionError):
            ops.run(self.core, "confirm", {"step": "1"}, caller="agent")
        with self.assertRaises(ValueError):
            ops.run(self.core, "confirm", {"step": "/procedures/cable-swap/1@r9"})
        ops.run(self.core, "confirm", {"step": "/procedures/cable-swap/1@r1"})
        self.assertTrue(self.core.state.steps["cable-swap/1"]["confirmed"])

    def test_only_local_hosts(self):
        check_url("http://localhost:8000/v1")
        check_url("http://192.168.55.1:8000/v1")
        with self.assertRaises(NotLocal):
            check_url("http://8.8.8.8/v1")


if __name__ == "__main__":
    unittest.main()
