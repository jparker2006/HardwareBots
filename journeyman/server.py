"""HTTP surface: the monitor UI, the headset protocol and operations. Local clients only."""
from __future__ import annotations

import base64
import ipaddress
import json
import os
import queue
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import ops
from .recorder import Recorder

UI = Path(__file__).parent / "ui" / "index.html"


def make_handler(core):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _local_client(self) -> bool:
            ip = ipaddress.ip_address(self.client_address[0].split("%")[0])
            return ip.is_loopback or ip.is_private or ip.is_link_local

        def _send(self, code: int, body, ctype: str = "application/json") -> None:
            data = body if isinstance(body, bytes) else (
                body.encode() if isinstance(body, str) else json.dumps(body, default=str).encode())
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def do_GET(self):
            if not self._local_client():
                return self._send(403, {"error": "local clients only"})
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if url.path == "/":
                return self._send(200, UI.read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/state":
                return self._send(200, core.dump())
            if url.path == "/api/events":
                return self._stream()
            if url.path == "/api/frame/latest.jpg":
                return self._send(200, core.eyes.latest, "image/jpeg") if core.eyes.latest else self._send(404, {})
            if url.path.startswith("/api/frames/"):
                fid = Path(url.path).stem
                f = core.state.frames.get(fid)
                return self._send(200, Path(f["path"]).read_bytes(), "image/jpeg") if f else self._send(404, {})
            if url.path == "/api/headset/outbox":
                after = int(q.get("after", ["-1"])[0])
                if after < 0:
                    return self._send(200, {"seq": core.state.last_seq, "items": []})
                items = core.wait_outbox(after, timeout=20)
                return self._send(200, {"seq": max([after] + [x["seq"] for x in items]), "items": items})
            if url.path == "/api/tools":
                return self._send(200, ops.tool_schemas())
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._local_client():
                return self._send(403, {"error": "local clients only"})
            url = urlparse(self.path)
            try:
                body = self._body()
                if url.path.startswith("/api/ops/"):
                    return self._send(200, {"result": ops.run(core, url.path.split("/")[-1], body)})
                if url.path == "/api/headset/utterance":
                    t0, t1 = body.get("t0") or time.time(), body.get("t1") or time.time()
                    if body.get("wav_b64"):
                        core.conductor.submit_audio(base64.b64decode(body["wav_b64"]), t0, t1)
                    elif body.get("text"):
                        core.conductor.submit_text(body["text"], t0, t1)
                    return self._send(202, {"accepted": True, "id": body.get("id")})
                if url.path == "/api/headset/voice":
                    if body.get("state") == "hearing":
                        core.voice.heard()
                    return self._send(200, {"voice_state": core.state.voice_state})
                if url.path == "/api/headset/said":
                    core.spoken(body.get("id", ""))
                    return self._send(200, {"ok": True})
                if url.path == "/api/frames":
                    fid = core.eyes.ingest(base64.b64decode(body["jpeg_b64"]))
                    return self._send(200, {"frame_id": fid})
                return self._send(404, {"error": "not found"})
            except (KeyError, ValueError, PermissionError) as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            events: queue.Queue = queue.Queue()
            core.listeners.append(events.put)
            try:
                self.wfile.write(f"event: snapshot\ndata: {core.dump()}\n\n".encode())
                self.wfile.flush()
                while True:
                    try:
                        ev = events.get(timeout=15)
                        self.wfile.write(f"data: {json.dumps(ev, default=str)}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if events.put in core.listeners:
                    core.listeners.remove(events.put)

    return Handler


def serve(core) -> None:
    if os.environ.get("JM_RECORD", "1") != "0":
        Recorder(core)
    core.start()
    server = ThreadingHTTPServer((core.config.host, core.config.port), make_handler(core))
    server.daemon_threads = True
    print(f"journeyman serving on http://{core.config.host}:{core.config.port}  gemma={core.config.gemma_url}  "
          f"camera={core.eyes.status()['source']}", flush=True)
    server.serve_forever()
