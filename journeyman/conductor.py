"""Orders events: voice commands, the capture loop, teaching. Holds no durable state."""
from __future__ import annotations

import queue
import re
import threading
import time
import uuid

from . import ops
from .gemma import text_part

COMMANDS = [
    ("start_capture", r"\b(start (a )?(capture|session|recording)|let'?s begin|watch me)\b"),
    ("start_teach", r"\b(new hire mode|teach mode|start (training|teaching)|teach me)\b"),
    ("end", r"\b(i'?m done|end (the )?session|that'?s it|stop (the )?session|we'?re done)\b"),
    ("next", r"^\W*(okay |ok |alright )?(next|done|finished|got it|ready)\W*$"),
    ("confirm", r"^\W*(yes|yep|correct|that'?s right)\W*(save it|confirm)?\W*$"),
]

OBSERVE_PROMPT = """You are watching a technician do a hands-on job, frame by frame, while they talk.
Steps already recorded: {known}
What they said in this window: {said}
Look at the frames (oldest first) and reply only JSON:
{{"new_steps": [{{"title": "<short imperative>", "action": "<what they did>", "cue": "<what to look for>", "frame_id": "<id>", "narrated": true|false}}],
  "unexplained": [{{"action": "<a deliberate action they did but gave no reason for>", "frame_id": "<id>", "confidence": 0.0-1.0}}]}}
Only include actions you can actually see. Do not repeat recorded steps. Empty lists are fine."""

QUESTION_PROMPT = """You are interviewing {expert} while they work. They just did this without saying why: "{action}".
Write one short, friendly spoken question asking why, under 20 words. Output only the question."""

WHY_PROMPT = """The expert was asked: "{question}" about the step "{step}". They answered: "{answer}".
Reply only JSON: {{"why": "<the reason in their words>", "consequence": "<what goes wrong if skipped, or empty>"}}"""


class Conductor:
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
            mistakes = len(s.sessions[sid]["mistakes"])
            self.core.say(f"Session over. {mistakes} slip{'s' if mistakes != 1 else ''} logged, and the card is updated.")
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
        if kind == "audio":
            try:
                text, secs, _path = self.core.ears.transcribe(payload)
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
        self.core.emit("utterance_heard", session_id=self.core.state.session_id, text=text, t0=t0, t1=t1)
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

    def observe(self, force: bool = False) -> dict | None:
        if not self._observing.acquire(blocking=force):
            return None
        try:
            s = self.core.state
            since = self.last_observe_at - 2
            self.last_observe_at, self.observed_seq = time.time(), s.last_seq
            frames = [f for f in s.frames.values() if f["t"] >= since][-6:]
            said = " / ".join(u["text"] for u in s.transcript(since)) or "(nothing)"
            if not frames:
                return None
            known = "; ".join(st.get("title", "") for st in s.procedure_steps(s.procedure or "")) or "(none)"
            content = self.core.eyes.images(frames) + [text_part(OBSERVE_PROMPT.format(known=known, said=said))]
            result, secs = self.core.vision.ask_json([{"role": "user", "content": content}], max_tokens=600)
            for step in result.get("new_steps", [])[:3]:
                if step.get("title") and step.get("action"):
                    ops.run(self.core, "save_step", {"title": step["title"], "action": step["action"],
                                                     "cue": step.get("cue"), "frame_id": step.get("frame_id"),
                                                     "source": "observed"})
            for gap in result.get("unexplained", [])[:2]:
                action, conf = gap.get("action", ""), float(gap.get("confidence") or 0)
                if not action or conf < self.cfg.gap_confidence:
                    continue
                if any(ops.similar(action, a) >= 0.5 for a in self.asked_actions + [c["action"] for c in self.candidates]):
                    continue
                self.core.emit("gap_noticed", action=action, frame_id=gap.get("frame_id"), confidence=conf)
                self.candidates.append({"action": action, "frame_id": gap.get("frame_id"), "at": time.time()})
            self.core.note("info", f"observed {len(frames)} frames in {secs:.1f}s")
            return result
        except Exception as e:
            self.core.note("error", f"observe: {e}")
            return None
        finally:
            self._observing.release()

    def maybe_ask(self, now: float) -> bool:
        s = self.core.state
        if not self.candidates or s.pending_question or self.core.speaking(now):
            return False
        if now - self.last_question_at < self.cfg.question_gap_s or now - self.last_utterance_end < self.cfg.silence_gate_s:
            return False
        gap = self.candidates.pop(0)
        if now - gap["at"] > 90:
            return False
        step = self._step_for(gap)
        try:
            msg, _ = self.core.gemma.chat([{"role": "user", "content": QUESTION_PROMPT.format(
                expert=self.cfg.expert_name, action=gap["action"])}], max_tokens=60, temperature=0.4)
            question = (msg.get("content") or "").strip().strip('"')
        except Exception:
            question = ""
        if not question or len(question) > 200:
            question = f"You {gap['action']} and didn't say why. Why?"
        qid = uuid.uuid4().hex[:8]
        self.asked_actions.append(gap["action"])
        self.last_question_at = now
        self.core.emit("question_asked", question_id=qid, text=question, about=gap["action"],
                       step_id=step["id"] if step else None)
        self.core.say(question)
        return True

    def _step_for(self, gap: dict) -> dict | None:
        steps = self.core.state.procedure_steps(self.core.state.procedure or "")
        if not steps:
            ops.run(self.core, "save_step", {"title": gap["action"][:60].capitalize(), "action": gap["action"],
                                             "frame_id": gap.get("frame_id"), "source": "observed"})
            steps = self.core.state.procedure_steps(self.core.state.procedure or "")
        best = max(steps, key=lambda st: ops.similar(st.get("action", "") + " " + st.get("title", ""), gap["action"]))
        if ops.similar(best.get("action", "") + " " + best.get("title", ""), gap["action"]) < 0.2:
            ops.run(self.core, "save_step", {"title": gap["action"][:60].capitalize(), "action": gap["action"],
                                             "frame_id": gap.get("frame_id"), "source": "observed"})
            best = self.core.state.procedure_steps(self.core.state.procedure or "")[-1]
        return best

    def attach_answer(self, text: str) -> None:
        q = self.core.state.pending_question
        self.core.emit("answer_attached", question_id=q["question_id"], step_id=q.get("step_id"), text=text)
        if not q.get("step_id"):
            return
        step = self.core.state.steps[q["step_id"]]
        try:
            parsed, _ = self.core.gemma.ask_json([{"role": "user", "content": WHY_PROMPT.format(
                question=q["text"], step=step.get("title"), answer=text)}], max_tokens=150)
        except Exception:
            parsed = {}
        ops.run(self.core, "save_why", {"step": step["id"], "why": parsed.get("why") or text,
                                        "consequence": parsed.get("consequence") or "", "source": "expert"})
        self.core.say("Got it, saved.")

    # ---- teach --------------------------------------------------------------
    def _teach_intro(self) -> None:
        s = self.core.state
        steps = s.procedure_steps(s.procedure or "")
        if not steps:
            self.core.say(f"I don't have any steps for {s.procedure} yet. Record an expert first.")
            return
        self.core.say(f"Let's do {s.procedure}. {len(steps)} steps. Say next when each one is done, and ask me why anytime.")
        self._say_step()

    def _say_step(self) -> None:
        s = self.core.state
        steps = s.procedure_steps(s.procedure or "")
        if s.teach_index >= len(steps):
            self.core.say("That's every step. Nice work.")
            self.end_session()
            return
        st = steps[s.teach_index]
        line = f"Step {st['n']}: {st.get('title')}."
        if st["warnings"]:
            line += f" Careful, people often slip here: {st['warnings'][-1]}."
        self.core.say(line)

    def teach_check(self) -> None:
        s = self.core.state
        steps = s.procedure_steps(s.procedure or "")
        if s.teach_index >= len(steps):
            return
        st = steps[s.teach_index]
        verdict = None
        if self.core.eyes.recent(15):
            try:
                verdict = self.core.eyes.look(f"Has this step been done: {st.get('action') or st.get('title')}?", 15).get("yes")
            except Exception as e:
                self.core.note("warn", f"look failed: {e}")
        if verdict is False:
            why = st["whys"][0]["why"] if st["whys"] else ""
            ops.run(self.core, "log_trainee", {"step": st["id"], "kind": "mistake",
                                               "detail": f"skipping {str(st.get('title')).lower()}"})
            self.core.say(f"Hold on, {st.get('title')} isn't done yet. {why}".strip())
            return
        ops.run(self.core, "log_trainee", {"step": st["id"], "kind": "done"})
        self._say_step()
