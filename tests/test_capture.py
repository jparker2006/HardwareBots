"""Capture mode: parsing, never nagging, mapping answers, and the offline prompt eval, against the fake Gemma."""
from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from journeyman import capture, cli, eval as jm_eval
from journeyman.capture import explained, imperative, parse_observation
from journeyman.config import Config
from journeyman.core import Core
from tests import fake_gemma

JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")
LABEL_GAP = {"new_steps": [], "unexplained": [{"action": "labels both ends of the new cable", "confidence": 0.9}]}


class ParseTest(unittest.TestCase):
    def test_parse_observation_is_forgiving(self):
        steps, gaps = parse_observation({
            "new_steps": [{"title": "The technician unplugs the cable.", "action": "pulls it out", "frame_id": "f1",
                           "narrated": "true"}, "junk", {"title": "", "action": ""}, {"action": "presses the latch"}],
            "unexplained": {"action": "tugs the cable", "frame_id": "nope", "confidence": "high"}}, ["f1"])
        self.assertEqual([s["title"] for s in steps], ["Unplug the cable", "Press the latch"])
        self.assertEqual(steps[0]["frame_id"], "f1")
        self.assertTrue(steps[0]["narrated"])
        self.assertEqual(gaps, [{"action": "tugs the cable", "frame_id": None, "confidence": 0.9}])
        self.assertEqual(parse_observation({"new_steps": "none", "unexplained": None}, []), ([], []))
        self.assertEqual(parse_observation({}, []), ([], []))

    def test_looking_is_not_a_step(self):
        steps, gaps = parse_observation({"new_steps": [
            {"title": "Observe cable connection", "action": "observes the cable connection"},
            {"title": "Look at the screen", "action": "looks at the screen"},
            {"title": "Read port label", "action": "looking at screen"},
            {"title": "Hold cable to camera", "action": "holds the cable up to the camera"},
            {"title": "Insert new cable", "action": "plugs in the new cable", "cue": "screen showing code/text"}],
            "unexplained": [{"action": "watches the screen", "confidence": 0.9}]}, [])
        self.assertEqual([(s["title"], s["cue"]) for s in steps], [("Insert new cable", None)])
        self.assertEqual(gaps, [])

    def test_imperative_and_explained(self):
        self.assertEqual(imperative("labels both ends"), "Label both ends")
        self.assertEqual(imperative("He pushes the clip"), "Push the clip")
        self.assertEqual(imperative("Press the latch"), "Press the latch")
        self.assertEqual(imperative("tidies the cable"), "Tidy the cable")
        self.assertTrue(explained("labels both ends of the cable", ["I label both ends so the next tech can trace it"]))
        self.assertFalse(explained("labels both ends of the cable", ["now I label both ends"]))
        self.assertFalse(explained("labels both ends", ["because the port is old"]))


class CaptureTest(unittest.TestCase):
    def setUp(self):
        self.server, self.script, url = fake_gemma.start()
        self.tmp = tempfile.mkdtemp()
        cfg = Config(gemma_url=url, data_dir=self.tmp, camera="none", observe_every_s=0, question_gap_s=20,
                     silence_gate_s=1.5, expert_name="Alex")
        self.core = Core(cfg, sync=True)
        self.c = self.core.conductor
        self.c.submit_text("start capture")
        self.core._speaking_until = 0

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def observe(self, reply, said=None):
        self.script.observe_replies.append(reply)
        if said:
            self.c.submit_text(said)
        self.core.eyes.ingest(JPEG)
        self.c.observe(force=True)

    def test_observe_prompt_lists_frame_ids_and_transcript(self):
        self.observe({"new_steps": [], "unexplained": []}, said="I pull the old cable out")
        text = " ".join(p.get("text", "") for p in self.script.requests[-1]["messages"][-1]["content"]
                        if p["type"] == "text")
        frame_id = next(iter(self.core.state.frames))
        self.assertIn(f"Frame {frame_id}", text)
        self.assertIn(f"Frames (oldest first): {frame_id}", text)
        self.assertIn("I pull the old cable out", text)

    def test_failed_observe_resends_its_frames_next_time(self):
        self.core.eyes.ingest(JPEG)
        self.c.clock = lambda: time.time() + 30
        with mock.patch.object(self.core.vision, "ask_json", side_effect=RuntimeError("took longer than 60s")):
            self.c.observe(force=True)
        first = next(iter(self.core.state.frames))
        self.c.observe(force=True)
        text = " ".join(p.get("text", "") for p in self.script.requests[-1]["messages"][-1]["content"]
                        if p["type"] == "text")
        self.assertIn(f"Frame {first}", text)

    def test_no_repeat_steps(self):
        step = {"title": "Unplug the old cable", "action": "unplugs the old network cable"}
        self.observe({"new_steps": [step], "unexplained": []})
        self.observe({"new_steps": [dict(step, title="Remove the old cable")], "unexplained": []})
        self.assertEqual(len(self.c._steps()), 1)

    def test_static_scene_for_60s_adds_no_steps(self):
        base, still, moved = time.time() + 1, b"\x10" * 64, b"\x90" * 64
        self.core.eyes.ingest(JPEG, at=base - 10, small=still)
        self.c.last_observe_at = base
        junk = {"new_steps": [{"title": "Insert new cable", "action": "plugs in the new cable"},
                              {"title": "Observe screen display", "action": "observes the screen"}],
                "unexplained": [{"action": "adjusts the cable", "confidence": 0.9}]}
        for k in range(1, 13):
            self.core.eyes.ingest(JPEG, at=base + 5 * k, small=still)
            if k % 2 == 0:
                self.script.observe_replies.append(junk)
                self.c.clock = lambda: base + 5 * k
                self.c.observe(force=True)
        self.assertEqual(self.c._steps(), [])
        self.assertEqual(self.c.candidates, [])
        self.assertTrue(any("static scene" in n["text"] for n in self.core.state.notes))
        self.script.observe_replies.append(junk)
        self.core.eyes.ingest(JPEG, at=base + 65, small=moved)
        self.c.clock = lambda: base + 66
        self.c.observe(force=True)
        self.assertEqual([st["title"] for st in self.c._steps()], ["Insert new cable"])

    def test_static_scene_with_narration_keeps_the_step(self):
        base, still = time.time() + 1, b"\x10" * 64
        self.core.eyes.ingest(JPEG, at=base - 10, small=still)
        self.c.last_observe_at = base - 1
        self.core.eyes.ingest(JPEG, at=base + 5, small=still)
        self.c.clock = lambda: base + 6
        self.observe({"new_steps": [{"title": "Leave a slack loop", "action": "leaves a slack loop"}]},
                     said="Now I leave a slack loop")
        self.assertEqual([st["title"] for st in self.c._steps()], ["Leave a slack loop"])

    def test_skips_gap_already_explained(self):
        self.observe(LABEL_GAP, said="I label both ends of the cable so the next tech can trace it")
        self.assertEqual(self.c.candidates, [])
        self.assertFalse(self.c.maybe_ask(time.time() + 30))
        self.assertTrue(any("already explained" in n["text"] for n in self.core.state.notes))

    def test_low_confidence_and_duplicates_are_dropped(self):
        self.observe({"unexplained": [{"action": "wiggles the plug", "confidence": 0.2}]})
        self.observe(LABEL_GAP)
        self.observe(LABEL_GAP)
        self.assertEqual([g["action"] for g in self.c.candidates], ["labels both ends of the new cable"])

    def test_one_question_at_a_time_with_rate_limit_and_silence_gate(self):
        self.observe(LABEL_GAP)
        self.observe({"unexplained": [{"action": "tugs the cable after plugging in", "confidence": 0.9}]})
        now = time.time()
        self.c.last_utterance_end = now
        self.assertFalse(self.c.maybe_ask(now + 0.5))
        self.assertTrue(self.c.maybe_ask(now + 2))
        self.core._speaking_until = 0
        self.assertFalse(self.c.maybe_ask(now + 30))
        self.assertEqual(len(self.core.state.questions), 1)
        self.c.attach_answer("so the next tech can trace it without pulling cables")
        self.core._speaking_until = 0
        self.assertFalse(self.c.maybe_ask(now + 10))
        self.assertTrue(self.c.maybe_ask(now + 25))
        self.assertEqual(len(self.core.state.questions), 2)

    def test_stale_gaps_and_unanswered_questions_expire(self):
        self.observe(LABEL_GAP)
        self.assertFalse(self.c.maybe_ask(time.time() + capture.GAP_TTL_S + 5))
        self.assertEqual(self.c.candidates, [])
        self.observe(dict(LABEL_GAP, unexplained=[{"action": "tugs the cable", "confidence": 0.9}]))
        self.assertTrue(self.c.maybe_ask(time.time() + 2))
        self.observe({"unexplained": [{"action": "blows dust out of the port", "confidence": 0.9}]})
        self.core._speaking_until = 0
        self.assertFalse(self.c.maybe_ask(time.time() + 30))
        self.c.candidates[0]["at"] = time.time() + 30
        self.assertTrue(self.c.maybe_ask(time.time() + capture.PENDING_TTL_S + 5))

    def test_answer_maps_to_named_step_in_expert_words(self):
        from journeyman import ops
        ops.run(self.core, "save_step", {"title": "Find the port by its label", "action": "reads the port label"})
        self.observe(LABEL_GAP)
        self.assertTrue(self.c.maybe_ask(time.time() + 2))
        self.script.why_replies.append({"step": 1, "why": "counting ports goes wrong on big switches",
                                        "consequence": "you pull a live server"})
        self.c.submit_text("Oh, the label thing, counting ports goes wrong on big switches")
        first = self.core.state.steps["cable-swap/1"]
        self.assertEqual(first["whys"][0]["why"], "counting ports goes wrong on big switches")
        self.assertEqual(first["whys"][0]["consequence"], "you pull a live server")
        self.assertEqual(self.core.state.said[-1]["text"], "Got it, saved.")

    def test_non_answer_keeps_question_open(self):
        self.observe(LABEL_GAP)
        self.assertTrue(self.c.maybe_ask(time.time() + 2))
        self.script.why_replies.append({"step": 1, "why": "", "consequence": ""})
        self.c.submit_text("now the new cable goes in")
        self.assertIsNotNone(self.core.state.pending_question)
        self.assertFalse(any(st["whys"] for st in self.c._steps()))
        self.c.submit_text("so the next tech can trace it")
        self.assertIsNone(self.core.state.pending_question)
        self.assertTrue(any(st["whys"] for st in self.c._steps()))

    def test_narration_echoed_as_why_is_not_an_answer(self):
        self.observe(LABEL_GAP)
        self.assertTrue(self.c.maybe_ask(time.time() + 2))
        self.script.why_replies.append({"gives_reason": False, "step": 1, "why": "Now I pull the old cable straight out"})
        self.c.submit_text("Now I pull the old cable straight out")
        self.assertIsNotNone(self.core.state.pending_question)
        self.assertFalse(any(st["whys"] for st in self.c._steps()))


class EvalTest(unittest.TestCase):
    def setUp(self):
        self.server, self.script, self.url = fake_gemma.start()
        self.rec = Path(tempfile.mkdtemp())
        (self.rec / "f1.jpg").write_bytes(JPEG)
        lines = [{"at": 0, "text": "Journeyman, start capture"}, {"at": 2, "frame": "f1.jpg"},
                 {"at": 3, "text": "First I pull the old cable out"}, {"at": 8, "frame": "f1.jpg"},
                 {"at": 30, "text": "So the next tech can trace it without pulling every cable"},
                 {"at": 40, "text": "I'm done"}, {"at": 45, "text": "teach me"}]
        (self.rec / "script.jsonl").write_text("\n".join(json.dumps(x) for x in lines))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_eval_runs_recording_in_virtual_time(self):
        out: list[str] = []
        t = time.time()
        cfg = Config(gemma_url=self.url, observe_every_s=10, question_gap_s=20, silence_gate_s=1.5)
        result = jm_eval.run(str(self.rec), cfg, out=out.append)
        self.assertLess(time.time() - t, 10)
        text = "\n".join(out)
        self.assertIn("gemma observe", text)
        self.assertIn("Q asked about", text)
        self.assertIn("= why", text)
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(len(result["questions"]), 1)
        self.assertIsNotNone(result["questions"][0]["answer"])
        self.assertNotIn("teach me", text)

    def test_cli_eval(self):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"JM_GEMMA_URL": self.url}), redirect_stdout(buf):
            cli.main(["eval", str(self.rec), "--every", "5"])
        self.assertIn("== steps", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
