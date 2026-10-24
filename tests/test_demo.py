"""`jm seed` and `jm reset`: a known-good card with no model call, and a clean start that keeps the old data."""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from journeyman import cli, demo, ops  # noqa: E402
from journeyman.config import Config  # noqa: E402
from journeyman.core import Core  # noqa: E402
from journeyman.server import make_handler  # noqa: E402

DEMO = Path(__file__).parent.parent / "recordings" / "demo"
DEAD = "http://127.0.0.1:9"


def jm(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            cli.main(list(argv))
            code = 0
        except SystemExit as e:
            code = e.code
    return code, out.getvalue() + err.getvalue()


class DemoTest(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp()) / "data"
        env = mock.patch.dict(os.environ, {"JM_DATA": str(self.data), "JM_GEMMA_URL": DEAD + "/v1"})
        env.start()
        self.addCleanup(env.stop)

    def core(self):
        return Core(Config(camera="none"), sync=True)

    def test_card_from_script_needs_no_model(self):
        rec = Path(tempfile.mkdtemp())
        shutil.copy(DEMO / "script.jsonl", rec / "script.jsonl")
        card = demo.load_card(rec)
        self.assertEqual([s["title"] for s in card["steps"]],
                         ["Find the port by its label", "Pull the old cable straight out", "New cable goes in"])
        self.assertEqual(card["steps"][2]["action"], "New cable goes in, and I leave a little loop of slack")
        (rec / "script.jsonl").write_text('{"at": 0, "text": "start capture"}\n{"at": 1, "text": "I label both ends"}\n'
                                          '{"at": 2, "text": "So the next tech can trace it"}\n{"at": 3, "text": "that\'s it"}\n'
                                          '{"at": 4, "text": "not a step"}\n')
        self.assertEqual(demo.load_card(rec)["steps"], [{"title": "Label both ends", "action": "Label both ends",
                                                         "why": "So the next tech can trace it"}])

    def test_seed_offline_then_teach_finds_the_reasons(self):
        code, out = jm("--url", DEAD, "seed", str(DEMO))
        self.assertEqual(code, 0, out)
        self.assertIn("seeded cable-swap: 4 steps, 4 reasons, confirmed", out)
        core = self.core()
        steps = core.state.procedure_steps("cable-swap")
        self.assertEqual(len(steps), 4)
        self.assertTrue(all(s["confirmed"] and s["whys"][0]["confirmed"] for s in steps))
        hit = ops.run(core, "search_knowledge", {"query": "why do I leave slack?"})["matches"][0]
        self.assertIn("pulling on the port", hit["whys"][0]["why"])
        self.assertIn("Label both ends", (self.data / "procedures" / "cable-swap.md").read_text())
        code, out = jm("--url", DEAD, "seed", str(DEMO))
        self.assertEqual(code, 1)
        self.assertIn("already has 4 steps", out)
        self.assertEqual(len(self.core().state.procedure_steps("cable-swap")), 4)

    def test_seed_through_running_service_and_reset_refuses(self):
        core = self.core()
        ui = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(core))
        threading.Thread(target=ui.serve_forever, daemon=True).start()
        self.addCleanup(ui.server_close)
        self.addCleanup(ui.shutdown)
        url = f"http://127.0.0.1:{ui.server_port}"
        code, out = jm("--url", url, "seed", str(DEMO / "card.json"), "--unconfirmed")
        self.assertEqual(code, 0, out)
        self.assertIn(url, out)
        self.assertEqual(len(core.state.procedure_steps("cable-swap")), 4)
        self.assertFalse(any(s["confirmed"] for s in core.state.procedure_steps("cable-swap")))
        code, out = jm("--url", url, "reset")
        self.assertEqual(code, 1)
        self.assertIn("Stop `jm serve` first", out)
        self.assertTrue((self.data / "events.jsonl").exists())

    def test_reset_archives_and_starts_clean(self):
        self.assertEqual(jm("--url", DEAD, "reset"), (0, f"{self.data} is already clean\n"))
        self.assertEqual(jm("--url", DEAD, "seed", str(DEMO))[0], 0)
        code, out = jm("--url", DEAD, "reset")
        self.assertEqual(code, 0, out)
        archives = list((self.data / "archive").iterdir())
        self.assertEqual(len(archives), 1)
        self.assertTrue((archives[0] / "events.jsonl").exists())
        self.assertEqual([p.name for p in self.data.iterdir()], ["archive"])
        self.assertEqual(self.core().state.procedures, {})
        code, out = jm("--url", DEAD, "reset", "--seed", str(DEMO))
        self.assertEqual(code, 0, out)
        self.assertIn("seeded cable-swap: 4 steps", out)
        self.assertIn("already clean", out)
        self.assertEqual(len(list((self.data / "archive").iterdir())), 1)
        self.assertEqual(len(self.core().state.procedure_steps("cable-swap")), 4)


if __name__ == "__main__":
    unittest.main()
