"""Agent against fake-Gemma variants: where tool calls land, bad replies, bounded loops, grounding."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from journeyman import ops  # noqa: E402
from journeyman.agent import NOT_CAPTURED, is_question, speakable  # noqa: E402
from journeyman.config import Config  # noqa: E402
from journeyman.core import Core  # noqa: E402
from journeyman.gemma import merge_roles, tool_calls  # noqa: E402
from tests import fake_gemma  # noqa: E402

WHY = "so the next tech can trace it without pulling cables"


def call(name, args, as_string=True):
    return {"content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
        "name": name, "arguments": json.dumps(args) if as_string else args}}]}


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.server, self.script, url = fake_gemma.start()
        cfg = Config(gemma_url=url, gemma_model="fake", data_dir=tempfile.mkdtemp(), camera="none",
                     expert_name="Alex", max_tool_calls=3)
        self.core = Core(cfg, sync=True)
        self.bodies = []

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def play(self, *replies):
        queue = list(replies)

        def reply(body):
            self.bodies.append(body)
            r = queue.pop(0) if len(queue) > 1 else queue[0]
            return r(body) if callable(r) else r
        self.script.reply = reply

    def capture(self):
        ops.run(self.core, "save_step", {"title": "Label both ends", "action": "labels both cable ends"})
        ops.run(self.core, "save_why", {"step": "1", "why": WHY})

    def said(self):
        return [x["text"] for x in self.core.state.said]

    def ask(self, text="Why do I label both ends?"):
        return self.core.agent.respond(text)

    def test_parsed_tool_call_with_dict_arguments(self):
        self.capture()
        self.play(call("search_knowledge", {"query": "label ends"}, as_string=False),
                  {"content": "**Alex says:** " + WHY + ". Also check the port. And more."})
        self.assertEqual(self.ask(), "Alex says: " + WHY + ". Also check the port.")
        self.assertEqual(self.said(), ["Alex says: " + WHY + ". Also check the port."])
        self.assertEqual(self.bodies[1]["messages"][-1]["role"], "tool")

    def test_tool_call_as_fenced_json_in_content(self):
        self.capture()
        fenced = '```json\n{"name": "search_knowledge", "arguments": "{\\"query\\": \\"label\\"}"}\n```'
        self.play({"content": fenced}, {"content": "Alex says: " + WHY + "."})
        self.ask()
        self.assertIn("trace", self.said()[-1])
        self.assertIn("Label both ends", self.bodies[1]["messages"][-1]["content"])

    def test_native_gemma_call_text_in_content(self):
        self.capture()
        self.play({"content": '<|tool_call>call:search_knowledge{query:<|"|>label both ends<|"|>}<tool_call|>'},
                  {"content": "Alex says: " + WHY + "."})
        self.ask()
        self.assertIn("trace", self.said()[-1])

    def test_unknown_tool_goes_back_as_error(self):
        self.capture()
        self.play(call("search_docs", {"q": "x"}), call("search_knowledge", {"query": "label"}),
                  {"content": "Alex says: " + WHY + "."})
        self.ask()
        self.assertIn("no tool named 'search_docs'", self.bodies[1]["messages"][-1]["content"])
        self.assertIn("trace", self.said()[-1])

    def test_400_with_tools_falls_back_to_no_tools(self):
        self.capture()
        self.play(lambda b: {"_status": 400, "error": "auto tool choice requires --enable-auto-tool-choice"}
                  if b.get("tools") else {"content": "Alex says: " + WHY + "."})
        self.ask()
        self.assertIn("trace", self.said()[-1])
        self.assertNotIn("tools", self.bodies[-1])
        notes = [e["data"]["text"] for e in self.core.journal.read() if e["type"] == "note"]
        self.assertTrue(any("400" in n for n in notes))

    def test_empty_reply_reads_the_experts_words(self):
        self.capture()
        self.play(call("search_knowledge", {"query": "label"}), {"content": ""})
        self.assertEqual(self.ask(), "Alex says: " + WHY + ".")

    def test_empty_reply_to_a_non_question(self):
        self.play({"content": None})
        self.assertEqual(self.ask("Journeyman, hmm"), "Sorry, I didn't catch that. Could you say it again?")

    def test_answer_without_search_is_not_trusted(self):
        self.play({"content": "Torque them to 5 Nm."})
        self.assertEqual(self.ask("What torque should the screws be?"), NOT_CAPTURED)
        self.assertEqual(len(self.core.state.open_questions), 1)

    def test_invented_answer_after_empty_search_is_replaced(self):
        self.play(call("search_knowledge", {"query": "torque"}), {"content": "Use 5 Nm."})
        self.assertEqual(self.ask("What torque should the screws be?"), NOT_CAPTURED)
        self.assertEqual(self.said(), [NOT_CAPTURED])
        self.assertEqual(len(self.core.state.open_questions), 1)

    def test_why_about_a_step_with_no_reason_is_not_captured(self):
        ops.run(self.core, "save_step", {"title": "Leave slack cable loop", "action": "leaves a slack loop in the cable"})
        self.play(call("search_knowledge", {"query": "slack"}),
                  {"content": "search_knowledge. The saved knowledge does not provide the reason for leaving slack."})
        self.assertEqual(self.ask("Why do I leave slack in the cable?"), NOT_CAPTURED)
        self.assertEqual(self.said(), [NOT_CAPTURED])
        self.assertEqual([q["question"] for q in self.core.state.open_questions.values()],
                         ["Why do I leave slack in the cable?"])

    def test_not_captured_logs_once(self):
        self.play(call("search_knowledge", {"query": "torque"}),
                  call("log_question", {"question": "What torque?"}), {"content": NOT_CAPTURED})
        self.ask("What torque should the screws be?")
        self.assertEqual(len(self.core.state.open_questions), 1)

    def test_loop_is_bounded(self):
        self.capture()
        self.play(call("search_knowledge", {"query": "label"}))
        self.assertEqual(self.ask(), "Alex says: " + WHY + ".")
        self.assertEqual(len(self.bodies), self.core.config.max_tool_calls + 1)

    def test_gemma_down_reads_saved_knowledge(self):
        self.capture()
        self.core.gemma.base = "http://127.0.0.1:9/v1"
        self.assertEqual(self.ask(), "Alex says: " + WHY + ".")

    def test_merge_mode_has_no_system_or_tool_roles(self):
        self.capture()
        self.core.agent.system_role = "merge"
        self.play(call("search_knowledge", {"query": "label"}), {"content": "Alex says: " + WHY + "."})
        self.ask()
        roles = [m["role"] for m in self.bodies[1]["messages"]]
        self.assertEqual(roles, ["user", "assistant", "user"])
        self.assertIn("You are Journeyman", self.bodies[0]["messages"][0]["content"])
        self.assertIn("Result of search_knowledge", self.bodies[1]["messages"][2]["content"])

    def test_helpers(self):
        self.assertEqual(speakable("## Step\n- **Pull** the `cable`.\n- Then [label](x) it."), "Step Pull the cable. Then label it.")
        self.assertEqual(speakable("<|channel>thought hmm<channel|>Yes.<turn|>"), "Yes.")
        self.assertEqual(speakable("search_knowledge. Based on the saved knowledge, Alex says: keep slack."),
                         "Alex says: keep slack.")
        self.assertEqual(speakable("I'll call log_question(question='x'). Look for the green light."),
                         "Look for the green light.")
        self.assertTrue(is_question("Journeyman, why label it"))
        self.assertFalse(is_question("Journeyman, save that"))
        self.assertEqual(tool_calls({"content": "call:log_question{question:<|\"|>a, b<|\"|>}"})[0]["arguments"],
                         {"question": "a, b"})
        self.assertEqual(tool_calls({"content": "The port is {left}."}), [])
        merged = merge_roles([{"role": "system", "content": "S"}, {"role": "user", "content": "U"},
                              {"role": "tool", "name": "t", "content": "1"}, {"role": "tool", "name": "u", "content": "2"}])
        self.assertEqual([m["role"] for m in merged], ["user"])


if __name__ == "__main__":
    unittest.main()
