"""A scripted stand-in for an OpenAI-compatible Gemma server, for tests only."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(p.get("text", "") for p in content or [] if p.get("type") == "text")


class Script:
    def __init__(self):
        self.observe_calls = 0
        self.look_calls = 0
        self.requests: list[dict] = []
        self.observe_replies: list[dict] = []
        self.why_replies: list[dict] = []

    def reply(self, body: dict) -> dict:
        self.requests.append(body)
        msgs = body["messages"]
        last = msgs[-1]
        text = _text(last.get("content"))
        if any(p.get("type") == "input_audio" for p in (last.get("content") or []) if isinstance(p, dict)):
            return {"content": "start capture"}
        if "new_steps" in text:
            self.observe_calls += 1
            if self.observe_replies:
                return {"content": json.dumps(self.observe_replies.pop(0))}
            if self.observe_calls == 1:
                return {"content": json.dumps({
                    "new_steps": [{"title": "Unplug the old cable", "action": "unplugs the old network cable",
                                   "cue": "port is empty", "frame_id": None, "narrated": True}],
                    "unexplained": [{"action": "labels both ends of the new cable", "frame_id": None, "confidence": 0.9}]})}
            return {"content": json.dumps({"new_steps": [], "unexplained": []})}
        if "friendly spoken question" in text:
            return {"content": "Why did you label both ends before plugging in?"}
        if '"why"' in text and "answered" in text:
            if self.why_replies:
                return {"content": json.dumps(self.why_replies.pop(0))}
            return {"content": json.dumps({"why": "so the next tech can trace it without pulling cables",
                                           "consequence": "someone unplugs the wrong server"})}
        if "Has this step been done" in text:
            self.look_calls += 1
            return {"content": json.dumps({"answer": "not yet" if self.look_calls == 1 else "yes",
                                           "yes": self.look_calls != 1, "frame_id": None})}
        if body.get("tools"):
            if last["role"] == "user":
                return {"content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "search_knowledge", "arguments": json.dumps({"query": text})}}]}
            if last["role"] == "tool" and last.get("name") == "search_knowledge":
                matches = json.loads(last["content"]).get("matches", [])
                if matches and matches[0]["whys"]:
                    return {"content": "Alex says: " + matches[0]["whys"][0]["why"] + "."}
                return {"content": "", "tool_calls": [{"id": "c2", "type": "function", "function": {
                    "name": "log_question", "arguments": json.dumps({"question": _text(msgs[1]["content"])})}}]}
            if last["role"] == "tool":
                return {"content": "That's not captured yet. I'll ask the expert next session."}
        return {"content": "OK"}


def start() -> tuple[ThreadingHTTPServer, Script, str]:
    script = Script()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj):
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._json({"data": [{"id": "fake-gemma"}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            msg = {"role": "assistant", **script.reply(body)}
            self._json({"choices": [{"message": msg}]})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, script, f"http://127.0.0.1:{server.server_port}/v1"
