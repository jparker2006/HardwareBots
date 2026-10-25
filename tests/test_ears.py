"""Headset --ears litert: Gemma E2B transcription on the Mac, and the fallback to sending audio (no model needed)."""
from __future__ import annotations

import array
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from headset import headset  # noqa: E402
from litert import ears  # noqa: E402


class FakeConversation:
    def __init__(self, engine):
        self.engine = engine

    def send_message(self, msg):
        self.engine.messages.append(msg)
        return {"role": "assistant", "content": [{"type": "text", "text": self.engine.replies.pop(0)}]}


class FakeEngine:
    def __init__(self, *replies):
        self.replies, self.messages, self.kwargs = list(replies), [], []

    def create_conversation(self, **kwargs):
        self.kwargs.append(kwargs)
        return FakeConversation(self)


class FakeProc:
    """Stands in for `ears.py --serve`: a ready line, then one reply line per request."""

    def __init__(self, lines):
        self.stdout, self.stdin, self.killed = io.StringIO("".join(json.dumps(x) + "\n" for x in lines)), io.StringIO(), False

    def poll(self):
        return None

    def kill(self):
        self.killed = True


class Link:
    def __init__(self):
        self.console, self.calls = headset.Console(io.StringIO()), []

    def call(self, path, body=None, **kw):
        self.calls.append((path, body))
        return {}


CLIP = array.array("h", [0] * 16000)


class EarsTest(unittest.TestCase):
    def test_transcribes_audio_with_fresh_conversation(self):
        eng = FakeEngine('"Why do I leave slack?"', "[no speech]")
        e = ears.Ears(engine=eng)
        self.assertEqual(e.transcribe(b"RIFF....")["text"], "Why do I leave slack?")
        self.assertEqual(e.transcribe(b"RIFF....")["text"], "")
        content = eng.messages[0]["content"]
        self.assertEqual(content[0]["type"], "audio")
        self.assertEqual(len(eng.kwargs), 2)

    def test_clean(self):
        self.assertEqual(ears.clean("Transcription: what's next"), "what's next")
        self.assertEqual(ears.clean("  (silence) "), "")
        self.assertEqual(ears.clean(""), "")

    def test_serve_protocol(self):
        out = io.StringIO()
        req = json.dumps({"wav_b64": "UklGRg=="}) + "\n\nnot json\n"
        ears.serve(lambda: ears.Ears(engine=FakeEngine("hello there")), io.StringIO(req), out)
        lines = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertTrue(lines[0]["ready"])
        self.assertEqual(lines[1]["text"], "hello there")
        self.assertIn("error", lines[2])

    def test_serve_reports_load_failure(self):
        def boom():
            raise RuntimeError("litert_lm is not installed")
        out = io.StringIO()
        self.assertEqual(ears.serve(boom, io.StringIO(""), out), 2)
        self.assertIn("not installed", json.loads(out.getvalue())["error"])


class HeadsetEarsTest(unittest.TestCase):
    def test_litert_text_goes_to_thor(self):
        link = Link()
        proc = FakeProc([{"ready": True, "load_s": 0.3}, {"text": "what's next", "seconds": 0.6}])
        e = headset.LiteRTEars(link.console, popen=lambda *a, **k: proc)
        self.assertTrue(e.ok)
        headset.send_clip(link, headset.Timing(), CLIP, 100.0, ears=e)
        path, body = link.calls[0]
        self.assertEqual(path, "/api/headset/utterance")
        self.assertEqual(body["text"], "what's next")
        self.assertNotIn("wav_b64", body)
        self.assertEqual((body["t0"], body["t1"]), (99.0, 100.0))

    def test_no_words_sends_nothing(self):
        link = Link()
        proc = FakeProc([{"ready": True}, {"text": "", "seconds": 0.5}])
        headset.send_clip(link, headset.Timing(), CLIP, 100.0, ears=headset.LiteRTEars(link.console,
                                                                                      popen=lambda *a, **k: proc))
        self.assertEqual(link.calls, [])

    def test_clip_error_falls_back_to_audio(self):
        link = Link()
        proc = FakeProc([{"ready": True}, {"error": "audio decode failed"}])
        e = headset.LiteRTEars(link.console, popen=lambda *a, **k: proc)
        headset.send_clip(link, headset.Timing(), CLIP, 100.0, ears=e)
        self.assertIn("wav_b64", link.calls[0][1])
        self.assertTrue(e.ok)

    def test_missing_venv_falls_back_to_audio(self):
        link = Link()
        e = headset.LiteRTEars(link.console, python="/nonexistent/jm-litert/bin/python")
        self.assertFalse(e.ok)
        self.assertIn("sending audio to Thor", link.console.out.getvalue())
        headset.send_clip(link, headset.Timing(), CLIP, 100.0, ears=e)
        self.assertIn("wav_b64", link.calls[0][1])

    def test_missing_litert_lm_falls_back(self):
        link = Link()
        old = os.environ.get("JM_LITERT_MODEL")
        os.environ["JM_LITERT_MODEL"] = "/nonexistent/model.litertlm"
        try:
            e = headset.LiteRTEars(link.console, python=sys.executable)
        finally:
            os.environ.pop("JM_LITERT_MODEL") if old is None else os.environ.__setitem__("JM_LITERT_MODEL", old)
        self.assertFalse(e.ok)
        self.assertIn("unavailable", link.console.out.getvalue())

    def test_unknown_ears_rejected(self):
        with self.assertRaises(SystemExit):
            headset.main(["--ears", "whisper"])


if __name__ == "__main__":
    unittest.main()
