"""Offline prompt eval: run a recording's frames and transcript through capture against the configured Gemma.

Time is virtual (script `at` seconds), so a 3 minute recording runs as fast as Gemma answers.
Prints steps, gaps, questions and answers with their script time and each Gemma call's latency.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from . import ops
from .config import Config
from .core import Core

SHOWN = {"step_saved", "gap_noticed", "question_asked", "answer_attached", "why_saved", "said", "note",
         "session_started", "session_ended", "utterance_heard"}


def _kind(messages: list[dict]) -> str:
    content = messages[-1].get("content")
    text = content if isinstance(content, str) else " ".join(p.get("text", "") for p in content or [] if isinstance(p, dict))
    if "new_steps" in text:
        return "observe"
    if "friendly spoken question" in text:
        return "question"
    if '"why"' in text:
        return "why"
    if "Transcribe" in text:
        return "ears"
    return "chat"


def _line(e: dict) -> str | None:
    d, kind = e["data"], e["type"]
    if kind == "step_saved":
        return f"+ step {d['step_id']}: {d.get('title')} | {d.get('action')} | cue: {d.get('cue')} | frame {d.get('frame_id')}"
    if kind == "gap_noticed":
        return f"? gap ({d.get('confidence')}): {d['action']} | frame {d.get('frame_id')}"
    if kind == "question_asked":
        return f"Q asked about {d.get('step_id')}: {d['text']}"
    if kind == "answer_attached":
        return f"A -> {d.get('step_id')}: {d['text']}"
    if kind == "why_saved":
        return f"= why {d['step_id']}: {d['why']} | if skipped: {d.get('consequence') or '-'}"
    if kind == "said":
        return f"> say: {d['text']}"
    if kind == "utterance_heard":
        return f"< heard: {d['text']}"
    if kind == "note":
        return f"  {d['level']}: {d['text']}"
    return f"# {kind} {d.get('kind', '')}"


def run(recording: str, cfg: Config | None = None, out=print, tail_s: float = 15.0, everything: bool = False) -> dict:
    root = Path(recording)
    lines = [json.loads(x) for x in (root / "script.jsonl").read_text().splitlines() if x.strip()]
    lines.sort(key=lambda x: x.get("at", 0))
    base = cfg or Config()
    tmp = tempfile.mkdtemp(prefix="jm-eval-")
    cfg = Config(**{**base.__dict__, "data_dir": Path(tmp), "camera": "none"})
    core = Core(cfg, sync=True)
    conductor = core.conductor
    clock = {"t": 0.0}
    start = time.time()
    vnow = lambda: start + clock["t"]  # noqa: E731
    conductor.clock = vnow
    calls: list[dict] = []

    def timed(g):
        chat = g.chat

        def wrapped(messages, **kw):
            t = time.time()
            try:
                return chat(messages, **kw)
            finally:
                calls.append({"kind": _kind(messages), "secs": time.time() - t})
                out(f"[{clock['t']:6.1f}s]   gemma {calls[-1]['kind']} {calls[-1]['secs']:.2f}s")
        g.chat = wrapped

    timed(core.gemma)
    if core.vision is not core.gemma:
        timed(core.vision)
    say = core.say

    def say_virtual(text):
        sid = say(text)
        core._speaking_until = vnow() + 1.0 + 0.07 * len(text)
        return sid
    core.say = say_virtual

    def show(e):
        if e["type"] in SHOWN:
            text = _line(e)
            if text:
                out(f"[{clock['t']:6.1f}s] {text}")
    core.listeners.append(show)

    out(f"eval {root} -> data {tmp}, gemma {cfg.gemma_url}, vision {cfg.vision_url}, observe every {cfg.observe_every_s}s")
    if not any("frame" in x for x in lines):
        out("note: no frames in this recording; observe needs frames, so no steps or gaps will appear")
    captured = False

    def advance(to: float) -> None:
        while clock["t"] + 1.0 <= to:
            clock["t"] += 1.0
            conductor.tick(now=vnow())
        clock["t"] = max(clock["t"], to)

    for line in lines:
        if captured and core.state.mode != "capture" and not everything:
            break
        advance(float(line.get("at", 0)))
        now, mode = vnow(), core.state.mode
        if "op" in line:
            out(f"[{clock['t']:6.1f}s] op {line['op']} -> {ops.run(core, line['op'], line.get('args', {}))}")
        elif "text" in line:
            conductor.submit_text(line["text"], now, now)
        elif "wav" in line:
            conductor.submit_audio((root / line["wav"]).read_bytes(), now, now)
        elif "frame" in line:
            core.eyes.ingest((root / line["frame"]).read_bytes(), at=now)
        if core.state.mode == "capture" and mode != "capture":
            conductor.last_observe_at = vnow()
            captured = True
    if core.state.mode == "capture":
        advance(clock["t"] + tail_s)
        conductor.end_session()

    s = core.state
    out("\n== steps")
    for name in s.procedures:
        for st in s.procedure_steps(name):
            out(f"{st['id']}: {st.get('title')} | {st.get('action')}")
            for w in st["whys"]:
                out(f"    why: {w['why']} | if skipped: {w.get('consequence') or '-'}")
    out(f"== {len(s.gaps)} gaps, {len(s.questions)} questions, "
        f"{sum(1 for q in s.questions.values() if q['answer'])} answered")
    for kind in sorted({c["kind"] for c in calls}):
        secs = [c["secs"] for c in calls if c["kind"] == kind]
        out(f"gemma {kind:8s} x{len(secs)}  avg {sum(secs) / len(secs):.2f}s  max {max(secs):.2f}s")
    return {"steps": [st for n in s.procedures for st in s.procedure_steps(n)], "gaps": s.gaps,
            "questions": list(s.questions.values()), "calls": calls, "data_dir": tmp}
