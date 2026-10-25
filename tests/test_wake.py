"""Live finding: the Mac ears hear 'journey man' as two words and people start with 'hello'."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from journeyman.agent import is_question  # noqa: E402
from journeyman.conductor import Conductor, normalize  # noqa: E402


class WakeWordTest(unittest.TestCase):
    def test_two_word_wake_word_is_joined(self):
        self.assertEqual(normalize("Hello journey man what is the first step"), "Hello Journeyman what is the first step")
        self.assertIn("Journeyman", normalize("hey Journey-Men, why?"))

    def test_fillers_do_not_hide_a_question(self):
        self.assertTrue(Conductor._asks("Hello Journeyman what is the first step"))
        self.assertTrue(Conductor._asks("okay so why do I leave slack"))
        self.assertFalse(Conductor._asks("now I pull the old cable out"))
        self.assertTrue(is_question("Hello Journeyman what is the first step"))
