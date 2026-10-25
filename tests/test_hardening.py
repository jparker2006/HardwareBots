"""Failure paths for the live server: timeouts, thinking text, JSON-mode fallback, image limits, concurrency,
plain on-screen errors and spoken failure lines. Runs against the fake Gemma."""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from journeyman import ops  # noqa: E402
from journeyman.config import Config  # noqa: E402
from journeyman.core import Core  # noqa: E402
from journeyman.ears import DEAF, NO_AUDIO  # noqa: E402
from journeyman.gemma import Gemma, GemmaError, image_part, text_part, without_thinking  # noqa: E402
from journeyman.teach import LOOK_DOWN  # noqa: E402
from tests import fake_gemma  # noqa: E402

JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")
WHY = "so the next tech can trace it without pulling cables"


class HardeningTest(unittest.TestCase):
    def setUp(self):
        self.server, self.script, self.url = fake_gemma.start()
        self.cfg = Config(gemma_url=self.url, gemma_model="fake", data_dir=tempfile.mkdtemp(), camera="none",
                          expert_name="Alex")
        self.core = Core(self.cfg, sync=True)
        self.bodies = []

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def play(self, fn):
        def reply(body):
            self.bodies.append(body)
            return fn(body)
        self.script.reply = reply

    def notes(self):
        return [e["data"]["text"] for e in self.core.journal.read() if e["type"] == "note"]

    def said(self):
        return [x["text"] for x in self.core.state.said]

    # ---- timeouts ---------------------------------------------------------
    def test_timeouts_per_call_kind_from_env(self):
        os.environ["JM_TIMEOUT_LOOK"] = "7"
        try:
            cfg = Config()
        finally:
            del os.environ["JM_TIMEOUT_LOOK"]
        self.assertEqual(cfg.timeouts["look"], 7.0)
        g = Gemma(self.url, "fake", cfg)
        self.assertEqual(g.timeout("look"), 7.0)
        self.assertEqual(g.timeout("agent"), cfg.timeouts["agent"])
        self.assertEqual(g.timeout("nonsense"), cfg.timeouts["default"])

    def test_slow_call_times_out_with_plain_message(self):
        self.play(lambda b: time.sleep(1.0) or {"content": "late"})
        self.core.gemma.timeouts["look"] = 0.2
        start = time.time()
        with self.assertRaises(GemmaError) as cm:
            self.core.gemma.chat([{"role": "user", "content": "hi"}], kind="look")
        self.assertLess(time.time() - start, 0.9)
        self.assertIn("Gemma (look) took longer than 0s", str(cm.exception))

    def test_server_down_says_so_plainly(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        g = Gemma(f"http://127.0.0.1:{port}/v1", "fake")
        with self.assertRaises(GemmaError) as cm:
            g.chat([{"role": "user", "content": "hi"}])
        self.assertIn("can't reach Gemma", str(cm.exception))
        self.assertIn("is the server running", str(cm.exception))

    # ---- thinking ---------------------------------------------------------
    def test_thinking_is_switched_off_and_stripped(self):
        self.assertEqual(without_thinking({"content": "<|channel>thought\nhmm<channel|>Hello."})["content"], "Hello.")
        self.assertEqual(without_thinking({"content": "<think>hmm</think> Hello."})["content"], "Hello.")
        self.assertEqual(without_thinking({"content": "hmm, let me see</think>Hello."})["content"], "Hello.")
        self.assertEqual(without_thinking({"content": "<|channel>thought\ncut off mid"})["content"], "")
        msg = without_thinking({"content": "Hi", "reasoning_content": "secret plan"})
        self.assertNotIn("reasoning_content", msg)
        self.play(lambda b: {"content": "Hi"})
        self.core.gemma.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(self.bodies[-1]["chat_template_kwargs"], {"enable_thinking": False})

    def test_thinking_never_reaches_speech(self):
        ops.run(self.core, "save_step", {"title": "Label both ends", "action": "labels both cable ends"})
        ops.run(self.core, "save_why", {"step": "1", "why": WHY})

        def reply(body):
            if body.get("tools") and body["messages"][-1]["role"] == "user":
                return {"content": "", "reasoning_content": "I should search.", "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "search_knowledge",
                                                                  "arguments": json.dumps({"query": "label"})}}]}
            return {"content": "<|channel>thought\nThe user wants the reason. Make one up?<channel|>Alex says: "
                               + WHY + ".", "reasoning_content": "Make one up?"}
        self.play(reply)
        self.core.agent.respond("Why do I label both ends?")
        self.assertTrue(self.said())
        self.assertIn("trace", self.said()[-1])
        self.assertFalse(any("make one up" in x.lower() or "thought" in x for x in self.said()))

    def test_thinking_never_reaches_json_parsing(self):
        self.play(lambda b: {"content": '<think>maybe {"why": "wrong"}</think>{"why": "right"}'})
        result, _ = self.core.gemma.ask_json([{"role": "user", "content": "x"}])
        self.assertEqual(result, {"why": "right"})

    def test_thinking_kwarg_dropped_if_server_rejects_it(self):
        def reply(body):
            if "chat_template_kwargs" in body:
                return {"_status": 400, "error": "Unrecognized request argument: chat_template_kwargs"}
            return {"content": "Hi"}
        self.play(reply)
        msg, _ = self.core.gemma.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(msg["content"], "Hi")
        self.core.gemma.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(len(self.bodies), 3)

    # ---- JSON mode ----------------------------------------------------------
    def test_json_mode_falls_back_when_unsupported(self):
        def reply(body):
            if "response_format" in body:
                return {"_status": 400, "error": "response_format is not supported"}
            return {"content": 'Sure! ```json\n{"yes": true}\n```'}
        self.play(reply)
        result, _ = self.core.gemma.ask_json([{"role": "user", "content": "x"}], kind="look")
        self.assertEqual(result, {"yes": True})
        self.core.gemma.ask_json([{"role": "user", "content": "x"}])
        self.assertEqual(len(self.bodies), 3)
        self.assertNotIn("response_format", self.bodies[-1])

    def test_json_mode_falls_back_on_bare_400(self):
        self.play(lambda b: {"_status": 400, "error": "bad request"} if "response_format" in b else {"content": '{"a": 1}'})
        self.assertEqual(self.core.gemma.ask_json([{"role": "user", "content": "x"}])[0], {"a": 1})

    def test_json_mode_off_never_sends_response_format(self):
        self.core.gemma.json_mode = "off"
        self.play(lambda b: {"content": '{"a": 1}'})
        self.core.gemma.ask_json([{"role": "user", "content": "x"}])
        self.assertNotIn("response_format", self.bodies[-1])

    def test_other_400s_still_raise(self):
        self.play(lambda b: {"_status": 400, "error": "image input is not supported"})
        with self.assertRaises(GemmaError) as cm:
            self.core.gemma.ask_json([{"role": "user", "content": "x"}])
        self.assertEqual(cm.exception.code, 400)
        self.assertIn("refused the request (400): image input is not supported", str(cm.exception))
        self.assertNotIn("response_format", self.core.gemma.unsupported)

    # ---- images -------------------------------------------------------------
    def test_images_are_capped_in_count_and_size(self):
        self.core.gemma.max_images, self.core.gemma.max_image_bytes = 2, 1000
        content = [image_part(JPEG + bytes([i])) for i in range(4)] + [image_part(b"x" * 5000), text_part("q")]
        self.play(lambda b: {"content": "{}"})
        self.core.gemma.ask_json([{"role": "user", "content": content}])
        sent = self.bodies[-1]["messages"][0]["content"]
        images = [p for p in sent if p["type"] == "image_url"]
        self.assertEqual(len(images), 2)
        self.assertEqual(images[-1], content[3])
        self.assertIn("(frame left out: too large)", fake_gemma._text(sent))
        self.assertEqual(len(content), 6)

    # ---- concurrency ----------------------------------------------------------
    def test_observe_and_agent_run_at_the_same_time(self):
        ops.run(self.core, "save_step", {"title": "Label both ends", "action": "labels both cable ends"})
        ops.run(self.core, "save_why", {"step": "1", "why": WHY})
        original = self.script.reply

        def slow(body):
            time.sleep(0.4)
            return original(body)
        self.script.reply = slow
        self.core.conductor.start_session("capture")
        self.core.eyes.ingest(JPEG)
        start = time.time()
        threads = [threading.Thread(target=self.core.conductor.observe, kwargs={"force": True}),
                   threading.Thread(target=self.core.agent.respond, args=("Why do I label both ends?",))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertLess(time.time() - start, 1.1)
        self.assertTrue(any("trace" in x for x in self.said()))
        self.assertTrue(any(n.startswith("observed") for n in self.notes()))

    # ---- what the user sees and hears -----------------------------------------
    def test_observe_failure_is_a_plain_note(self):
        self.play(lambda b: {"_status": 500, "error": "CUDA out of memory"})
        self.core.conductor.start_session("capture")
        self.core.eyes.ingest(JPEG)
        self.assertIsNone(self.core.conductor.observe(force=True))
        self.assertIn("couldn't read the camera frames: Gemma (observe) refused the request (500): CUDA out of memory",
                      self.notes())

    def test_agent_survives_gemma_down(self):
        ops.run(self.core, "save_step", {"title": "Label both ends", "action": "labels both cable ends"})
        ops.run(self.core, "save_why", {"step": "1", "why": WHY})
        self.core.gemma.base = "http://127.0.0.1:9/v1"
        reply = self.core.agent.respond("Why do I label both ends?")
        self.assertIn("trace", reply)
        self.assertTrue(any("can't reach Gemma" in n for n in self.notes()))

    def test_agent_timeout_does_not_wait_twice(self):
        ops.run(self.core, "save_step", {"title": "Label both ends", "action": "labels both cable ends"})
        ops.run(self.core, "save_why", {"step": "1", "why": WHY})
        self.play(lambda b: time.sleep(0.6) or {"content": "late"})
        self.core.gemma.timeouts["agent"] = 0.2
        start = time.time()
        self.assertIn("trace", self.core.agent.respond("Why do I label both ends?"))
        self.assertLess(time.time() - start, 0.5)
        self.assertEqual(len(self.bodies), 1)

    def test_ears_failure_is_spoken_once(self):
        self.script.reject_audio = True
        self.core.conductor.submit_audio(b"RIFF", 0, 1)
        self.core.conductor.submit_audio(b"RIFF", 0, 1)
        self.assertEqual(self.said(), [NO_AUDIO])
        self.assertTrue(any("ears: Gemma (ears) refused the request (400)" in n for n in self.notes()))
        self.core.ears._said_failure_at = 0
        self.core.gemma.base = "http://127.0.0.1:9/v1"
        self.core.conductor.submit_audio(b"RIFF", 0, 1)
        self.assertEqual(self.said()[-1], DEAF)

    def test_teach_says_when_camera_check_is_down(self):
        ops.run(self.core, "save_step", {"title": "Unplug the old cable", "action": "unplugs it", "cue": "port empty",
                                         "procedure": "cable-swap"})
        self.core.conductor.submit_text("teach me")
        self.core.eyes.ingest(JPEG)
        self.play(lambda b: {"_status": 503, "error": "server busy"})
        self.core.conductor.submit_text("done")
        self.assertEqual(self.said()[-1], LOOK_DOWN)
        self.assertTrue(any("camera check failed: Gemma (look)" in n for n in self.notes()))


if __name__ == "__main__":
    unittest.main()


class OllamaStructuredOutputTest(unittest.TestCase):
    def test_ollama_501_structured_output_drops_json_mode(self):
        from journeyman.gemma import Gemma, GemmaError
        g = Gemma("http://127.0.0.1:9/v1", "m")
        err = GemmaError("Gemma (look) refused the request (501): structured output is unavailable", code=501)
        self.assertEqual(g._rejected(err, {"response_format": {"type": "json_object"}}), "response_format")
