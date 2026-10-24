"""LiteRT answer.py logic against a scripted engine (no model, no litert_lm needed)."""
import unittest
from pathlib import Path

from litert import answer

CARD = (Path(__file__).resolve().parent.parent / "litert" / "sample_card.md").read_text()


class FakeConversation:
    def __init__(self, engine):
        self.engine = engine

    def send_message(self, text):
        self.engine.prompts.append(text)
        return {"role": "assistant", "content": [{"type": "text", "text": self.engine.replies.pop(0)}]}

    def close(self):
        self.engine.closed += 1


class FakeEngine:
    def __init__(self, *replies):
        self.replies, self.prompts, self.kwargs, self.closed = list(replies), [], [], 0

    def create_conversation(self, **kwargs):
        self.kwargs.append(kwargs)
        return FakeConversation(self)


class AnswerTest(unittest.TestCase):
    def test_answers_from_card(self):
        eng = FakeEngine("**Slack** takes the strain off the connector. It won't work loose. Also more text.")
        r = answer.Answerer(engine=eng).answer(CARD, "why do I leave slack?")
        self.assertTrue(r["captured"])
        self.assertEqual(r["answer"], "Slack takes the strain off the connector. It won't work loose.")
        self.assertIn("Insert the new cable with slack", eng.prompts[0])
        self.assertIn("QUESTION: why do I leave slack?", eng.prompts[0])
        self.assertIn(answer.MARKER, eng.kwargs[0]["system_message"])
        self.assertEqual(eng.closed, 1)

    def test_not_captured(self):
        for reply in ("NOT CAPTURED.", "That's not captured on this card.", ""):
            r = answer.Answerer(engine=FakeEngine(reply)).answer(CARD, "what torque?")
            self.assertEqual(r, {"answer": answer.NOT_CAPTURED, "captured": False, "seconds": r["seconds"]})

    def test_empty_card_never_calls_model(self):
        eng = FakeEngine()
        r = answer.Answerer(engine=eng).answer("  ", "why?")
        self.assertFalse(r["captured"])
        self.assertEqual(eng.prompts, [])

    def test_unconfirmed_reason_gets_caveat(self):
        r = answer.Answerer(engine=FakeEngine("Pulling at an angle bends the pins in the port.")).answer(CARD, "why straight?")
        self.assertTrue(r["answer"].endswith(answer.UNCONFIRMED))
        r = answer.Answerer(engine=FakeEngine("Slack takes the strain off the connector.")).answer(CARD, "why slack?")
        self.assertNotIn(answer.UNCONFIRMED, r["answer"])

    def test_ask_reuses_one_engine(self):
        eng = FakeEngine("Read the label.", "So the next person can trace it.")
        answer._shared = answer.Answerer(engine=eng)
        try:
            self.assertEqual(answer.ask("port?", card_text=CARD)["answer"], "Read the label.")
            self.assertTrue(answer.ask("labels?", card_text=CARD)["captured"])
            self.assertEqual(len(eng.kwargs), 2)
        finally:
            answer._shared = None

    def test_missing_model_is_a_clear_error(self):
        with self.assertRaises(RuntimeError):
            answer.Answerer(model_path="/nonexistent.litertlm")


if __name__ == "__main__":
    unittest.main()
