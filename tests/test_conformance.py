"""Format bugs found against a real Gemma 4 (docs/CONFORMANCE.md), replayed against the fake."""
from __future__ import annotations

import unittest

from journeyman import doctor
from journeyman.gemma import Gemma
from tests import fake_gemma


class ConformanceTest(unittest.TestCase):
    def setUp(self):
        self.server, self.script, self.url = fake_gemma.start()
        self.gemma = Gemma(self.url)

    def tearDown(self):
        self.server.shutdown()

    def test_asks_for_no_thinking(self):
        self.gemma.chat([{"role": "user", "content": "hi"}])
        body = self.script.requests[-1]
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})

    def test_server_rejecting_thinking_fields_gets_plain_requests(self):
        self.script.reject_fields = ["reasoning_effort"]
        msg, _ = self.gemma.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(msg["content"], "OK")
        self.gemma.chat([{"role": "user", "content": "again"}])
        self.assertEqual(len(self.script.requests), 2)
        self.assertNotIn("reasoning_effort", self.script.requests[-1])

    def test_doctor_probes_ask_for_no_thinking(self):
        doctor.chat(self.url, "fake-gemma", "Say OK.", max_tokens=5)
        self.assertEqual(self.script.requests[-1]["reasoning_effort"], "none")


if __name__ == "__main__":
    unittest.main()
