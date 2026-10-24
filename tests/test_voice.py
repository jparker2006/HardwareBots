"""Voice state transitions, the thinking filler, and the headset's Thor address picking."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from headset import headset  # noqa: E402
from journeyman.config import Config  # noqa: E402
from journeyman.core import Core  # noqa: E402
from journeyman.server import make_handler  # noqa: E402
from journeyman.voice import FILLERS  # noqa: E402
from tests import fake_gemma  # noqa: E402

LATER = 10_000.0


class VoiceStateTest(unittest.TestCase):
    def setUp(self):
        self.server, self.script, url = fake_gemma.start()
        self.addCleanup(self.server.shutdown)
        self.tmp = tempfile.mkdtemp()
        self.core = Core(Config(gemma_url=url, data_dir=self.tmp, camera="none", observe_every_s=0,
                                question_gap_s=0, silence_gate_s=0), sync=True)
        self.voice = self.core.voice

    def states(self):
        return [e["data"]["state"] for e in self.core.journal.read() if e["type"] == "voice_state"]

    def said(self):
        return [e["data"]["text"] for e in self.core.journal.read() if e["type"] == "said"]

    def test_starts_listening_and_is_in_the_snapshot(self):
        self.assertEqual(json.loads(self.core.dump())["voice_state"], "listening")

    def test_text_turn_thinks_then_speaks_then_listens(self):
        self.core.conductor.submit_text("Journeyman, start capture")
        self.assertEqual(self.states(), ["thinking", "speaking"])
        self.voice.poll(now=time.time() + LATER)
        self.assertEqual(self.states(), ["thinking", "speaking", "listening"])
        self.assertEqual(self.core.state.voice_state, "listening")

    def test_audio_turn_hears_first(self):
        self.core.conductor.submit_audio(b"RIFF....", time.time() - 1, time.time())
        self.assertEqual(self.states()[:3], ["hearing", "thinking", "speaking"])

    def test_unaddressed_narration_goes_back_to_listening_without_filler(self):
        self.core.conductor.submit_text("start capture")
        self.core._speaking_until = 0.0
        self.voice.poll()
        before = len(self.said())
        self.core.conductor.submit_text("now I loosen the two screws")
        self.voice.poll(now=time.time() + LATER)
        self.assertEqual(self.states()[-2:], ["thinking", "listening"])
        self.assertEqual(len(self.said()), before)

    def test_filler_after_four_seconds_once_per_turn(self):
        t = time.time()
        with self.voice.turn():
            self.voice.thinking(now=t)
            self.voice.poll(now=t + 3.0)
            self.assertEqual(self.said(), [])
            self.voice.poll(now=t + 4.1)
            self.assertEqual(self.said(), [FILLERS[0]])
            self.voice.thinking(now=t + 5)
            self.voice.poll(now=t + 20)
            self.assertEqual(self.said(), [FILLERS[0]])
        self.assertIn("speaking", self.states())
        with self.voice.turn():
            self.voice.thinking(now=t + 30)
            self.voice.poll(now=t + 35)
        self.assertEqual(self.said(), [FILLERS[0], FILLERS[1]])

    def test_no_filler_when_the_reply_comes_first(self):
        t = time.time()
        with self.voice.turn():
            self.voice.thinking(now=t)
            self.core.say("Label both ends.")
            self.voice.poll(now=t + 10)
        self.assertEqual(self.said(), ["Label both ends."])

    def test_headset_hearing_shows_then_goes_stale(self):
        t = time.time()
        self.voice.heard(now=t)
        self.assertEqual(self.core.state.voice_state, "hearing")
        self.voice.poll(now=t + 25)
        self.assertEqual(self.core.state.voice_state, "listening")

    def test_restart_comes_back_listening(self):
        with self.voice.turn():
            self.voice.thinking()
        self.core.emit("voice_state", state="thinking")
        again = Core(self.core.config, sync=True)
        self.assertEqual(again.state.voice_state, "listening")

    def test_headset_voice_endpoint(self):
        ui = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.core))
        threading.Thread(target=ui.serve_forever, daemon=True).start()
        self.addCleanup(ui.shutdown)
        base = f"http://127.0.0.1:{ui.server_port}"
        req = Request(base + "/api/headset/voice", json.dumps({"state": "hearing"}).encode(),
                      {"Content-Type": "application/json"})
        self.assertEqual(json.loads(urlopen(req).read())["voice_state"], "hearing")
        self.assertEqual(json.loads(urlopen(base + "/api/state").read())["voice_state"], "hearing")


class Quiet:
    def __init__(self):
        self.lines = []

    def line(self, text):
        self.lines.append(text)


class HeadsetAddressTest(unittest.TestCase):
    def test_default_order_is_usb_c_first(self):
        urls = headset.addresses(headset.THOR)
        self.assertIn("192.168.55.1", urls[0])
        self.assertIn("USB-C", headset.describe(urls[0]))

    def test_pick_first_reachable(self):
        urls = ["http://192.168.55.1:3000", "http://192.168.3.131:3000"]
        self.assertEqual(headset.pick(urls, lambda u: True), urls[0])
        self.assertEqual(headset.pick(urls, lambda u: "131" in u), urls[1])
        self.assertIsNone(headset.pick(urls, lambda u: False))

    def test_choose_says_which(self):
        con = Quiet()
        link = headset.Link(headset.THOR, con, probe=lambda u: "131" in u)
        self.assertTrue(link.choose())
        self.assertIn("192.168.3.131", link.thor)
        self.assertIn("network", con.lines[-1])
        self.assertIn("not answering", con.lines[-1])

    def test_switches_to_usb_c_when_wifi_goes(self):
        up = {"http://192.168.3.131:3000"}
        con = Quiet()
        link = headset.Link(headset.THOR, con, probe=lambda u: u in up)
        link.choose()
        calls = []

        def fake_request(url, body=None, timeout=30.0):
            calls.append(url)
            if not any(url.startswith(u) for u in up):
                raise urllib.error.URLError("no route to host")
            return {"ok": True}

        real, headset.request_json = headset.request_json, fake_request
        self.addCleanup(setattr, headset, "request_json", real)
        up.clear()
        up.add("http://192.168.55.1:3000")
        out = link.call("/api/ops/status", {}, retry_s=3)
        self.assertEqual(out, {"ok": True})
        self.assertEqual(link.thor, "http://192.168.55.1:3000")
        self.assertTrue(any("switched to USB-C" in x for x in con.lines))


if __name__ == "__main__":
    unittest.main()
