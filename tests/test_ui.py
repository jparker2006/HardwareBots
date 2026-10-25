"""The monitor page: served locally, offline-only assets, and the state fields its counters read."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from journeyman import ops  # noqa: E402
from journeyman.config import Config  # noqa: E402
from journeyman.core import Core  # noqa: E402
from journeyman.server import UI, make_handler  # noqa: E402
from tests import fake_gemma  # noqa: E402


class UITest(unittest.TestCase):
    def test_index_redirects_to_the_final_page(self):
        self.assertIn("/ui/final/", UI.read_text())

    def test_final_page_is_offline_and_uses_the_core_api(self):
        final = UI.parent / "final"
        text = "".join((final / n).read_text() for n in ("index.html", "app.js", "style.css", "planet.js", "ascii.js", "qr.js"))
        self.assertIsNone(re.search(r"https?://(?!github\.com/jparker2006)|//cdn|@import", text))
        self.assertEqual(re.findall(r"url\(([^)]*)\)", text),
                         ["'./fonts/geist.woff2'", "'./fonts/jetbrains-mono.woff2'", "'./fonts/newsreader.woff2'"])
        for needle in ("/api/state", "/api/events", "/api/ops/status", "/api/frame/latest.jpg", "/api/frames/",
                       "/api/headset/utterance", "no cloud", "unconfirmed", "common mistake", "slips caught",
                       "open questions", "voice_state", "showModal"):
            self.assertIn(needle, text)

    def test_served_page_and_session_counters(self):
        server, _script, url = fake_gemma.start()
        self.addCleanup(server.shutdown)
        core = Core(Config(gemma_url=url, data_dir=tempfile.mkdtemp(), camera="none"), sync=True)
        ui = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(core))
        threading.Thread(target=ui.serve_forever, daemon=True).start()
        self.addCleanup(ui.shutdown)
        base = f"http://127.0.0.1:{ui.server_port}"
        self.assertIn(b"/ui/final/", urlopen(base + "/").read())
        for path in ("/ui/final/", "/ui/final/app.js", "/ui/final/qr.js", "/ui/final/fonts/geist.woff2"):
            self.assertEqual(urlopen(base + path).status, 200)
        urlopen(base + "/api/headset/utterance", json.dumps({"text": "typed fallback"}).encode())
        ops.run(core, "session_start", {"kind": "capture"})
        ops.run(core, "save_step", {"title": "Label both ends", "action": "labels the cable"})
        ops.run(core, "save_why", {"step": "1", "why": "so it can be traced"})
        s = json.loads(urlopen(base + "/api/state").read())
        sess = s["sessions"][s["session_id"]]
        step = s["procedures"][s["procedure"]]["steps"][0]
        self.assertGreaterEqual(step["created"], sess["started"])
        self.assertGreaterEqual(step["whys"][0]["t"], sess["started"])


if __name__ == "__main__":
    unittest.main()


class StaticUiTest(unittest.TestCase):
    def test_ui_files_are_served_and_traversal_is_refused(self):
        import tempfile, threading, urllib.request, urllib.error
        from http.server import ThreadingHTTPServer
        from journeyman.config import Config
        from journeyman.core import Core
        from journeyman.server import make_handler
        core = Core(Config(gemma_url="http://127.0.0.1:9/v1", data_dir=tempfile.mkdtemp(), camera="none"), sync=True)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(core))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_port}"
        try:
            self.assertEqual(urllib.request.urlopen(base + "/ui/index.html").status, 200)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + "/ui/../server.py")
        finally:
            srv.shutdown()
