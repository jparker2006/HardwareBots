"""JM_PROFILE=thor31b (short calls for the slow 31B) and the look caption that is always a sentence."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from journeyman.config import Config
from journeyman.core import Core
from journeyman.eyes import as_yes, caption
from tests import fake_gemma

JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


def env(**values):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("JM_")}
    return mock.patch.dict(os.environ, dict(clean, **values), clear=True)


class ProfileConfigTest(unittest.TestCase):
    def test_no_profile_keeps_defaults(self):
        with env():
            cfg = Config()
        self.assertEqual((cfg.max_images, cfg.max_image_kb, cfg.frame_width, cfg.observe_every_s), (6, 512, 512, 10.0))
        self.assertEqual((cfg.max_tokens, cfg.compact_json, cfg.profile), ({}, False, ""))
        self.assertEqual(cfg.timeouts["observe"], 60.0)

    def test_thor31b_sets_tuned_values(self):
        with env(JM_PROFILE="thor31b"):
            cfg = Config()
        self.assertEqual(cfg.profile, "thor31b")
        self.assertEqual((cfg.max_images, cfg.max_image_kb, cfg.frame_width, cfg.observe_every_s), (3, 96, 384, 20.0))
        self.assertTrue(cfg.compact_json)
        self.assertEqual(cfg.max_tokens, {"observe": 220, "look": 60, "agent": 160, "why": 90, "question": 30})
        self.assertEqual((cfg.timeouts["observe"], cfg.timeouts["look"], cfg.timeouts["default"]), (75.0, 30.0, 90.0))

    def test_explicit_env_beats_profile_and_typos_fail(self):
        with env(JM_PROFILE="THOR31B", JM_MAX_IMAGES="2", JM_TIMEOUT_LOOK="12", JM_MAX_TOKENS_LOOK="40"):
            cfg = Config()
        self.assertEqual((cfg.max_images, cfg.timeouts["look"], cfg.max_tokens["look"]), (2, 12.0, 40))
        with env(JM_PROFILE="thor13b"), self.assertRaises(ValueError):
            Config()


class ProfileCallsTest(unittest.TestCase):
    def setUp(self):
        self.server, self.script, url = fake_gemma.start()
        with env(JM_PROFILE="thor31b"):
            cfg = Config(gemma_url=url, gemma_model="fake", data_dir=tempfile.mkdtemp(), camera="none",
                         observe_every_s=0, expert_name="Alex")
        self.core = Core(cfg, sync=True)
        self.c = self.core.conductor

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def images(self, body):
        return [p for p in body["messages"][-1]["content"] if p["type"] == "image_url"]

    def test_observe_sends_few_frames_short_reply_compact_json(self):
        self.c.submit_text("start capture")
        for i in range(8):
            self.core.eyes.ingest(JPEG + bytes([i]))
        self.c.observe(force=True)
        body = self.script.requests[-1]
        self.assertEqual(body["max_tokens"], 220)
        self.assertEqual(len(self.images(body)), 3)
        self.assertIn("one line of JSON", fake_gemma._text(body["messages"][-1]["content"]))

    def test_look_is_capped_and_caption_is_a_sentence(self):
        for i in range(5):
            self.core.eyes.ingest(JPEG + bytes([i]))
        self.script.look_answers = [False]
        result = self.core.eyes.look("Has this step been done: unplug the cable?")
        body = self.script.requests[-1]
        self.assertEqual((body["max_tokens"], len(self.images(body))), (60, 3))
        self.assertIn("short sentence", fake_gemma._text(body["messages"][-1]["content"]))
        self.assertIs(result["yes"], False)
        self.assertEqual(result["answer"], "No, that isn't done yet.")
        self.assertEqual(self.core.state.observed["text"], "No, that isn't done yet.")

    def test_other_kinds_without_a_cap_are_untouched(self):
        self.core.gemma.chat([{"role": "user", "content": "hi"}], max_tokens=300, kind="ears")
        self.assertEqual(self.script.requests[-1]["max_tokens"], 300)
        self.core.gemma.chat([{"role": "user", "content": "hi"}], max_tokens=20, kind="agent")
        self.assertEqual(self.script.requests[-1]["max_tokens"], 20)


class CaptionTest(unittest.TestCase):
    def test_bare_answers_become_sentences(self):
        self.assertEqual(caption("yes", True), "Yes, that looks done.")
        self.assertEqual(caption("False", False), "No, that isn't done yet.")
        self.assertEqual(caption("", None), "I can't tell from the camera.")
        self.assertEqual(caption(None, None), "I can't tell from the camera.")
        self.assertEqual(caption("not yet.", False), "No, that isn't done yet.")

    def test_real_sentences_are_kept(self):
        self.assertEqual(caption("the cable is in port 12.", True), "The cable is in port 12.")
        self.assertEqual(caption("Both ends labeled", True), "Both ends labeled")

    def test_yes_field_is_a_bool_or_none(self):
        self.assertEqual([as_yes(v) for v in (True, "yes", "False", None, "maybe", 1)],
                         [True, True, False, None, None, None])


if __name__ == "__main__":
    unittest.main()
