"""Teach mode by voice: step lines, commands, camera check, skip and retry paths. Runs against the fake Gemma."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from journeyman import ops  # noqa: E402
from journeyman.config import Config  # noqa: E402
from journeyman.core import Core  # noqa: E402
from tests import fake_gemma  # noqa: E402

JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


class TeachTest(unittest.TestCase):
    def setUp(self):
        self.server, self.script, url = fake_gemma.start()
        cfg = Config(gemma_url=url, data_dir=tempfile.mkdtemp(), camera="none", expert_name="Alex")
        self.core = Core(cfg, sync=True)
        self.c = self.core.conductor

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def seed(self):
        for title, action, cue in [("Unplug the old cable", "unplugs the old network cable", "port one is empty"),
                                   ("Label both ends", "labels both ends of the new cable", "a tag on each end"),
                                   ("Plug in the new cable", "plugs the new cable into port one", "link light is green")]:
            ops.run(self.core, "save_step", {"title": title, "action": action, "cue": cue, "procedure": "cable-swap"})
        ops.run(self.core, "save_why", {"step": "cable-swap/2", "why": "So the next tech can trace it. Also it looks neat."})
        self.core.emit("trainee_mistake", session_id="", step_id="cable-swap/2", detail="labelling only one end")

    def teach(self):
        self.seed()
        self.c.submit_text("teach me")
        self.assertEqual(self.core.state.mode, "teach")

    def last(self) -> str:
        return self.core.state.said[-1]["text"]

    def look_prompts(self):
        return [fake_gemma._text(r["messages"][-1]["content"]) for r in self.script.requests
                if "Has this step been done" in fake_gemma._text(r["messages"][-1]["content"])]

    def test_step_line_has_action_cue_and_warning(self):
        self.teach()
        self.assertIn("Step 1 of 3: Unplug the old cable.", self.last())
        self.assertIn("Alex unplugs the old network cable", self.last())
        self.assertIn("looking for: port one is empty", self.last())
        self.script.look_answers = [True]
        self.core.eyes.ingest(JPEG)
        self.c.submit_text("done")
        self.assertEqual(self.core.state.teach_index, 1)
        self.assertIn("common mistake here: labelling only one end", self.last())
        self.assertIn("port one is empty", self.look_prompts()[-1])

    def test_repeat_back_left_whats_next(self):
        self.teach()
        first = self.last()
        self.c.submit_text("repeat that")
        self.assertEqual(self.last(), first)
        self.c.submit_text("back")
        self.assertEqual(self.core.state.teach_index, 0)
        self.assertEqual(self.last(), first)
        self.c.submit_text("How many steps are left?")
        self.assertIn("3 steps left", self.last())
        self.c.submit_text("What's next?")
        self.assertIn("step 2: label both ends", self.last())
        self.script.look_answers = [True]
        self.core.eyes.ingest(JPEG)
        self.c.submit_text("next")
        self.c.submit_text("previous step")
        self.assertEqual(self.core.state.teach_index, 0)
        self.assertIn("Step 1 of 3", self.last())
        self.assertEqual(self.core.state.open_questions, {})

    def test_skip_logs_slip_and_moves_on(self):
        self.teach()
        self.c.submit_text("skip")
        self.assertEqual(self.core.state.teach_index, 1)
        self.assertEqual(self.core.state.session()["mistakes"][0]["detail"], "skipping unplug the old cable")
        self.assertIn("Step 2 of 3", self.last())
        self.c.submit_text("skip this one")
        self.c.submit_text("how many left")
        self.assertEqual(self.last(), "This is the last step.")
        self.c.submit_text("what's next")
        self.assertIn("you're finished", self.last())

    def test_caught_skip_says_why_and_stays(self):
        self.teach()
        self.script.look_answers = [True, False]
        self.core.eyes.ingest(JPEG)
        self.c.submit_text("done")
        self.c.submit_text("done")
        self.assertEqual(self.core.state.teach_index, 1)
        self.assertEqual(self.last(), "Hold on, label both ends isn't done yet. Alex's reason: So the next tech can trace it.")
        self.assertEqual(len(self.core.state.session()["mistakes"]), 1)

    def test_unsure_retries_once_then_accepts_with_note(self):
        self.teach()
        self.script.look_answers = [None, None]
        self.core.eyes.ingest(JPEG)
        self.c.submit_text("done")
        self.assertEqual(self.core.state.teach_index, 0)
        self.assertIn("Hold it up to the camera", self.last())
        self.c.submit_text("done")
        self.assertEqual(self.core.state.teach_index, 1)
        self.assertTrue(any("without a camera check" in n["text"] for n in self.core.state.notes))
        self.assertTrue(any("take your word" in x["text"] for x in self.core.state.said))

    def test_unsure_then_seen_on_retry(self):
        self.teach()
        self.script.look_answers = [None, False]
        self.core.eyes.ingest(JPEG)
        self.c.submit_text("done")
        self.c.submit_text("done")
        self.assertEqual(self.core.state.teach_index, 0)
        self.assertIn("isn't done yet", self.last())

    def test_finish_summary_ends_session(self):
        self.teach()
        self.script.look_answers = [True, True]
        self.core.eyes.ingest(JPEG)
        self.c.submit_text("done")
        self.c.submit_text("skip")
        self.c.submit_text("done")
        self.assertEqual(self.core.state.mode, "idle")
        self.assertEqual(self.last(), "All done: 2 of 3 steps checked off and 1 slip logged, and the card is updated.")

    def test_no_steps_suggests_capture(self):
        self.c.submit_text("teach me")
        self.assertIn("start capture", self.last())
        self.assertEqual(self.core.state.mode, "idle")
        self.c.submit_text("start capture")
        self.assertEqual(self.core.state.mode, "capture")


if __name__ == "__main__":
    unittest.main()
