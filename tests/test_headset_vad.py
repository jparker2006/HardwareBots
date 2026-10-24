"""Headset speech detection on synthetic audio, plus the headset's pure helpers. VAD tests skip without numpy."""
from __future__ import annotations

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from headset import headset  # noqa: E402

try:
    import numpy as np
    from headset.vad import Vad, rms
except ImportError:
    np = None

RATE, BLOCK = 16000, 480


def tone(seconds, amp):
    t = np.arange(int(seconds * RATE)) / RATE
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.int16)


def noise(seconds, amp, seed=0):
    return np.random.default_rng(seed).normal(0, amp, int(seconds * RATE)).clip(-32000, 32000).astype(np.int16)


def run(vad, audio):
    out = []
    for i in range(0, len(audio), BLOCK):
        utt = vad.feed(audio[i:i + BLOCK])
        if utt is not None:
            out.append(utt)
    return out


@unittest.skipIf(np is None, "numpy not installed")
class VadTest(unittest.TestCase):
    def test_silence_and_room_noise_send_nothing(self):
        self.assertEqual(run(Vad(), np.zeros(3 * RATE, np.int16)), [])
        self.assertEqual(run(Vad(floor=100), noise(3, 100)), [])

    def test_one_phrase_becomes_one_utterance_with_trimmed_tail(self):
        audio = np.concatenate([noise(1, 50), tone(1.0, 3000) + noise(1, 50, 1), noise(1.5, 50, 2)])
        [utt] = run(Vad(), audio)
        self.assertFalse(utt.cut)
        self.assertAlmostEqual(utt.silence_s, 0.8, delta=0.05)
        self.assertGreater(utt.seconds, 1.0)
        self.assertLess(utt.seconds, 1.0 + 0.45 + 0.2 + 0.05)
        self.assertEqual(utt.samples.dtype, np.int16)

    def test_clicks_shorter_than_start_are_ignored(self):
        audio = np.concatenate([np.zeros(RATE, np.int16), tone(0.06, 8000), np.zeros(2 * RATE, np.int16)])
        self.assertEqual(run(Vad(), audio), [])

    def test_short_pause_keeps_one_utterance_long_pause_splits(self):
        word, gap = tone(0.5, 3000), np.zeros(int(0.4 * RATE), np.int16)
        tail = np.zeros(RATE, np.int16)
        self.assertEqual(len(run(Vad(), np.concatenate([word, gap, word, tail]))), 1)
        long_gap = np.zeros(int(1.2 * RATE), np.int16)
        self.assertEqual(len(run(Vad(), np.concatenate([word, long_gap, word, tail]))), 2)

    def test_nonstop_talking_is_cut_at_max_seconds(self):
        utts = run(Vad(max_s=2.0), tone(5.0, 3000))
        self.assertGreaterEqual(len(utts), 2)
        self.assertTrue(utts[0].cut)
        self.assertAlmostEqual(utts[0].seconds, 2.0, delta=0.01)

    def test_calibration_and_floor_follows_a_louder_room(self):
        vad = Vad()
        self.assertEqual(vad.calibrate(noise(1, 40)), 300.0)
        vad.calibrate(noise(1, 200))
        self.assertAlmostEqual(vad.threshold, 600, delta=30)
        self.assertEqual(run(vad, noise(4, 400, 3)), [])
        self.assertGreater(vad.threshold, 1000)
        [utt] = run(vad, np.concatenate([tone(1.0, 6000) + noise(1, 400, 4), noise(1.5, 400, 5)]))
        self.assertGreater(utt.seconds, 1.0)

    def test_fixed_threshold_does_not_move(self):
        vad = Vad(threshold=800)
        run(vad, noise(3, 500))
        self.assertEqual(vad.threshold, 800)

    def test_reset_drops_speech_in_progress(self):
        vad = Vad()
        run(vad, tone(0.5, 3000))
        self.assertTrue(vad.active)
        vad.reset()
        self.assertEqual(run(vad, np.zeros(2 * RATE, np.int16)), [])

    def test_rms(self):
        self.assertEqual(rms(np.zeros(0, np.int16)), 0.0)
        self.assertAlmostEqual(rms(tone(1, 1000)), 1000 / 2 ** 0.5, delta=2)


class HelpersTest(unittest.TestCase):
    def test_meter_marks_threshold_and_level(self):
        quiet, loud = headset.meter(10, 1000), headset.meter(5000, 1000, "REC")
        self.assertIn("|", quiet)
        self.assertGreater(loud.count("#"), quiet.count("#"))
        self.assertTrue(loud.endswith("REC"))

    def test_resync_follows_a_fresh_thor_down(self):
        self.assertEqual(headset.resync(40, 3), 3)
        self.assertEqual(headset.resync(40, 52), 40)

    def test_timing_reports_once(self):
        t = headset.Timing()
        self.assertIsNone(t.reply_started())
        t.ended(100.0)
        self.assertAlmostEqual(t.reply_started(now=102.5), 2.5)
        self.assertIsNone(t.reply_started(now=103.0))

    def test_say_command_voice_and_rate(self):
        s = headset.Speaker(None, headset.Timing(), voice="Samantha", rate=210)
        self.assertEqual(s.command("hi"), ["say", "-v", "Samantha", "-r", "210", "hi"])
        self.assertEqual(headset.Speaker(None, headset.Timing()).command("hi"), ["say", "hi"])

    def test_link_reports_outage_and_recovery_once(self):
        calls = {"n": 0}

        def flaky(url, body=None, timeout=30.0):
            calls["n"] += 1
            if calls["n"] <= 3:
                raise ConnectionRefusedError("refused")
            return {"ok": True}

        out = io.StringIO()
        link = headset.Link("http://127.0.0.1:1/", headset.Console(out))
        real, headset.request_json = headset.request_json, flaky
        try:
            with self.assertRaises(ConnectionRefusedError):
                link.call("/x")
            with self.assertRaises(ConnectionRefusedError):
                link.call("/x")
            self.assertEqual(link.call("/x", retry_s=5), {"ok": True})
        finally:
            headset.request_json = real
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("unreachable", lines[0])
        self.assertIn("reconnected", lines[1])

    def test_pick_device(self):
        self.assertIsNone(headset.pick_device(None))
        self.assertEqual(headset.pick_device("2"), 2)
        self.assertEqual(headset.pick_device("USB"), "USB")


if __name__ == "__main__":
    unittest.main()
