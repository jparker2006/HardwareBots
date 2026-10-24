"""Capture mode: watch the expert, record steps, ask about what they didn't explain, save the answers."""
from __future__ import annotations

import time
import uuid

from . import ops
from .gemma import text_part

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


class CaptureMixin:
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
