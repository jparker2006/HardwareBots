"""Records each live session's raw inputs as a replayable script, and plays scripts back."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

ENDS_SESSION = {"end", "start_capture", "start_teach"}


def load_script(root: Path) -> list[dict]:
    lines = [json.loads(x) for x in (Path(root) / "script.jsonl").read_text().splitlines() if x.strip()]
    return sorted(lines, key=lambda x: x.get("at", 0))


def summarize(root: Path) -> dict:
    lines = load_script(root)
    meta_path = Path(root) / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    return {"name": Path(root).name, "duration_s": max([x.get("at", 0) for x in lines] or [0]),
            "utterances": sum(1 for x in lines if "text" in x or "wav" in x),
            "audio": sum(1 for x in lines if "wav" in x), "frames": sum(1 for x in lines if "frame" in x),
            "ops": sum(1 for x in lines if "op" in x), "kind": meta.get("kind"), "procedure": meta.get("procedure")}


def play(root: Path, send, speed: float = 1.0, after=None) -> int:
    """Calls send(kind, value, line) for each line at its time; kind is text, wav (bytes), frame (bytes) or op."""
    root, start, n = Path(root), time.time(), 0
    for line in load_script(root):
        wait = line.get("at", 0) / speed - (time.time() - start)
        if wait > 0:
            time.sleep(wait)
        if "op" in line:
            send("op", line["op"], line)
        elif "text" in line:
            send("text", line["text"], line)
        elif "wav" in line:
            send("wav", (root / line["wav"]).read_bytes(), line)
        elif "frame" in line:
            send("frame", (root / line["frame"]).read_bytes(), line)
        else:
            continue
        n += 1
        if after:
            after()
    return n


def replay_into(core, root: Path, speed: float = 1000.0, tick: bool = True) -> int:
    """Feed a recording straight into an in-process Core (tests, prompt eval). Ticks the conductor after each line."""
    from . import ops

    def send(kind, value, line):
        if kind == "op":
            ops.run(core, value, line.get("args", {}))
        elif kind == "text":
            core.conductor.submit_text(value)
        elif kind == "wav":
            now = time.time()
            core.conductor.submit_audio(value, now, now)
        else:
            core.eyes.ingest(value)

    return play(root, send, speed, after=core.conductor.tick if tick else None)


class Recorder:
    """Listens to core events; while a session is live, writes <data>/recordings/<session_id>/script.jsonl."""

    def __init__(self, core, root: Path | None = None):
        self.core = core
        self.root = Path(root or core.config.data_dir / "recordings")
        self.dir: Path | None = None
        self.session_id: str | None = None
        self.started = 0.0
        self.last_line: dict | None = None
        self.last_utterance: tuple | None = None
        if core.state.session_id:
            s = core.state.sessions[core.state.session_id]
            self._open(s["id"], s["started"], s)
        core.listeners.append(self.on_event)

    def on_event(self, event: dict) -> None:
        try:
            self._on_event(event)
        except Exception as e:
            print(f"recorder: {type(e).__name__}: {e}", file=sys.stderr, flush=True)

    def _on_event(self, event: dict) -> None:
        kind, d, t = event["type"], event["data"], event["t"]
        if kind == "session_started":
            self._open(d["session_id"], t, d)
            u = self.last_utterance
            if u and t - u[0]["t"] < 5 and self.core.conductor.command(u[0]["data"]["text"]) in ENDS_SESSION:
                self._utterance(u[0], u[1], at=0.0)
            else:
                self._write({"at": 0.0, "op": "session_start", "args": {
                    k: d.get(k) for k in ("kind", "procedure", "trainee") if d.get(k)}})
            self.last_utterance = None
        elif kind == "session_ended" and d["session_id"] == self.session_id:
            last = self.last_line or {}
            if not ("text" in last or "wav" in last) or \
                    self.core.conductor.command(last.get("heard") or last.get("text", "")) not in ENDS_SESSION:
                self._write({"at": self._at(t), "op": "session_end"})
            self._close(t)
        elif kind == "utterance_heard":
            self.last_utterance = (event, d.get("audio"))
            if self.dir:
                self._utterance(event, d.get("audio"))
        elif kind == "frame_kept" and self.dir:
            name = f"frames/{d['frame_id']}.jpg"
            self._copy(d["path"], name)
            self._write({"at": self._at(d.get("at") or t), "frame": name})

    def _open(self, session_id: str, started: float, info: dict) -> None:
        self.session_id, self.started, self.last_line = session_id, started, None
        self.dir = self.root / session_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta = {"session_id": session_id, "kind": info.get("kind"), "procedure": info.get("procedure"),
                     "trainee": info.get("trainee"), "started": started, "ended": None}
        self._save_meta()

    def _close(self, t: float) -> None:
        self.meta.update(ended=t, duration_s=self._at(t))
        self._save_meta()
        self.dir, self.session_id = None, None

    def _save_meta(self) -> None:
        (self.dir / "meta.json").write_text(json.dumps(self.meta, indent=2) + "\n")

    def _utterance(self, event: dict, audio: str | None, at: float | None = None) -> None:
        at = self._at(event["t"]) if at is None else at
        text = event["data"]["text"]
        if audio and os.path.exists(audio):
            name = f"audio/{Path(audio).name}"
            self._copy(audio, name)
            self._write({"at": at, "wav": name, "heard": text})
        else:
            self._write({"at": at, "text": text})

    def _copy(self, src: str, name: str) -> None:
        dest = self.dir / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)

    def _at(self, t: float) -> float:
        return round(max(0.0, t - self.started), 2)

    def _write(self, line: dict) -> None:
        with (self.dir / "script.jsonl").open("a") as f:
            f.write(json.dumps(line) + "\n")
        self.last_line = line
