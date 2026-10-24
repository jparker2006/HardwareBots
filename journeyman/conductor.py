"""Orders events: voice commands, the capture loop, teaching. Holds no durable state."""
from __future__ import annotations

import queue
import re
import threading
import time
import uuid

from .capture import CaptureMixin
from .teach import TeachMixin

COMMANDS = [
    ("start_capture", r"\b(start (a )?(capture|session|recording)|let'?s begin|watch me)\b"),
    ("start_teach", r"\b(new hire mode|teach mode|start (training|teaching)|teach me)\b"),
    ("end", r"\b(i'?m done|end (the )?session|that'?s it|stop (the )?session|we'?re done)\b"),
    ("next", r"^\W*(okay |ok |alright )?(next|done|finished|got it|ready)\W*$"),
    ("confirm", r"^\W*(yes|yep|correct|that'?s right)\W*(save it|confirm)?\W*$"),
    ("teach_repeat", r"^\W*(can you |could you |please )?(repeat( that| it)?|say (that|it) again|again|come again)\W*(please)?\W*$"),
    ("teach_back", r"^\W*(go )?(back|previous)( (a |one )?step)?( please)?\W*$"),
    ("teach_skip", r"^\W*(let'?s )?skip( (it|this|that|this one|this step))?( please)?\W*$"),
    ("teach_left", r"\bhow many (more )?(steps )?(are )?(left|to go|remaining|more)\b"),
    ("teach_whats_next", r"\b(what'?s next|what (is|comes) next|what do i do next)\b"),
]


class Conductor(CaptureMixin, TeachMixin):
    def __init__(self, core, sync: bool = False):
        self.core = core
        self.cfg = core.config
        self.sync = sync
        self.inbox: queue.Queue = queue.Queue()
        self.last_utterance_end = 0.0
        self.last_question_at = 0.0
        self.last_observe_at = 0.0
        self.observed_seq = 0
        self.candidates: list[dict] = []
        self.asked_actions: list[str] = []
        self._observing = threading.Lock()

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> None:
        threading.Thread(target=self._brain_loop, daemon=True).start()
        threading.Thread(target=self._tick_loop, daemon=True).start()

    def start_session(self, kind: str, procedure: str | None = None, trainee: str | None = None) -> str:
        if self.core.state.session_id:
            self.end_session()
        sid = time.strftime("%H%M%S") + "-" + uuid.uuid4().hex[:4]
        name = procedure or self.core.state.procedure or self.cfg.default_procedure
        self.core.emit("session_started", session_id=sid, kind=kind, procedure=name, trainee=trainee)
        self.candidates, self.asked_actions = [], []
        self.last_observe_at = time.time()
        self.observed_seq = self.core.state.last_seq
        if kind == "capture":
            self.core.say(f"Recording {name}. Work as you normally would and talk me through it.")
        else:
            self._teach_intro()
        return sid

    def end_session(self) -> str | None:
        s = self.core.state
        sid, kind, name = s.session_id, s.mode, s.procedure
        if not sid:
            return None
        if kind == "capture":
            self.observe(force=True)
        self.core.emit("session_ended", session_id=sid)
        steps = s.procedure_steps(name or "")
        whys = sum(len(st["whys"]) for st in steps)
        if kind == "capture":
            self.core.say(f"Saved {len(steps)} step{'s' if len(steps) != 1 else ''} and {whys} reason{'s' if whys != 1 else ''} for {name}.")
        else:
            self.core.say(self._teach_summary(sid))
        return sid

    # ---- input ------------------------------------------------------------
    def submit_audio(self, wav: bytes, t0: float, t1: float) -> None:
        self._enqueue(("audio", wav, t0, t1))

    def submit_text(self, text: str, t0: float | None = None, t1: float | None = None) -> None:
        now = time.time()
        self._enqueue(("text", text, t0 or now, t1 or now))

    def _enqueue(self, item) -> None:
        if self.sync:
            self._handle(item)
        else:
            self.inbox.put(item)

    def _brain_loop(self) -> None:
        while True:
            item = self.inbox.get()
            try:
                self._handle(item)
            except Exception as e:
                self.core.note("error", f"brain: {e}")

    def _handle(self, item) -> None:
        kind, payload, t0, t1 = item
        extra = {}
        if kind == "audio":
            try:
                text, secs, extra["audio"] = self.core.ears.transcribe(payload)
            except Exception as e:
                self.core.note("error", f"ears: {e}")
                return
            self.core.note("info", f"heard in {secs:.1f}s")
        else:
            text = payload
        text = text.strip()
        if not text:
            return
        self.last_utterance_end = t1
        self.core.emit("utterance_heard", session_id=self.core.state.session_id, text=text, t0=t0, t1=t1, **extra)
        self.on_utterance(text)

    # ---- routing ----------------------------------------------------------
    def command(self, text: str) -> str | None:
        low = text.lower()
        for name, pattern in COMMANDS:
            if re.search(pattern, low):
                return name
        return None

    def on_utterance(self, text: str) -> None:
        s = self.core.state
        cmd = self.command(text)
        if cmd == "start_capture" and s.mode != "capture":
            self.start_session("capture")
            return
        if cmd == "start_teach" and s.mode != "teach":
            self.start_session("teach")
            return
        if cmd == "end" and s.mode != "idle":
            self.end_session()
            return
        if s.mode == "capture":
            if s.pending_question and time.time() - s.pending_question["t"] < 60:
                self.attach_answer(text)
            elif self._addressed(text):
                self.core.agent.respond(text)
        elif s.mode == "teach":
            if cmd == "next":
                self.teach_check()
            elif cmd and cmd.startswith("teach_"):
                self.teach_command(cmd)
            elif self._addressed(text) or "?" in text or re.match(r"^\s*(why|how|what|where|when|which|should)\b", text.lower()):
                self.core.agent.respond(text)
        elif self._addressed(text):
            self.core.agent.respond(text)

    @staticmethod
    def _addressed(text: str) -> bool:
        return "journeyman" in text.lower() or text.strip().endswith("?")

    # ---- capture ------------------------------------------------------------
    def _tick_loop(self) -> None:
        while True:
            time.sleep(1.0)
            try:
                self.tick()
            except Exception as e:
                self.core.note("error", f"tick: {e}")

    def tick(self, now: float | None = None) -> None:
        now = now or time.time()
        s = self.core.state
        if s.mode != "capture":
            return
        if now - self.last_observe_at >= self.cfg.observe_every_s and s.last_seq > self.observed_seq:
            if self.sync:
                self.observe()
            else:
                threading.Thread(target=self.observe, daemon=True).start()
        self.maybe_ask(now)
