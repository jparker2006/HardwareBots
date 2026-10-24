"""Teach mode: give the steps by voice, check each one with the camera, log slips."""
from __future__ import annotations

import re

from . import ops

LOOK_PROMPT = "Has this step been done: {title}? Judge it from the most recent frame. Look for: {cue}."


def _sentence(text: str) -> str:
    first = re.split(r"(?<=[.!?])\s+", (text or "").strip())[0].rstrip(".!? ")
    return first + "." if first else ""


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class TeachMixin:
    def _steps(self) -> list[dict]:
        s = self.core.state
        return s.procedure_steps(s.procedure or "")

    def _current(self) -> dict | None:
        steps, i = self._steps(), self.core.state.teach_index
        return steps[i] if 0 <= i < len(steps) else None

    def _move_to(self, index: int) -> None:
        self._teach_retry = None
        self.core.emit("teach_moved", session_id=self.core.state.session_id, index=index)

    def _teach_intro(self) -> None:
        s = self.core.state
        self._teach_retry = None
        steps = self._steps()
        if not steps:
            self.core.say(f"I don't have any steps for {s.procedure} yet. Say start capture and record an expert first.")
            self.core.emit("session_ended", session_id=s.session_id)
            return
        self.core.say(f"Let's do {s.procedure}. {_plural(len(steps), 'step')}. Say done when each one is finished. "
                      "You can say repeat, back, or skip, and ask me why anytime.")
        self._say_step()

    def _step_line(self, st: dict) -> str:
        title = str(st.get("title") or st["id"]).rstrip(".")
        line = f"Step {st['n']} of {len(self._steps())}: {title}."
        action = (st.get("action") or "").strip().rstrip(".")
        if action and ops.similar(action, title) < 0.6:
            verb = action.split()[0].lower()
            who = f"{self.cfg.expert_name} " if verb.endswith("s") and not verb.endswith("ss") else ""
            line += f" {who}{action}."
        if st.get("cue"):
            line += f" You're looking for: {str(st['cue']).rstrip('.')}."
        if st["warnings"]:
            line += f" Heads up, a common mistake here: {str(st['warnings'][-1]).rstrip('.')}."
        return line

    def _say_step(self) -> None:
        st = self._current()
        if st is None:
            self.end_session()
            return
        self.core.say(self._step_line(st))

    def _teach_summary(self, sid: str) -> str:
        s = self.core.state
        sess = s.sessions[sid]
        total = len(s.procedure_steps(sess["procedure"]))
        done, slips = len(sess["done_steps"]), len(sess["mistakes"])
        lead = "All done" if s.teach_index >= total else "Session over"
        return f"{lead}: {done} of {_plural(total, 'step')} checked off and {_plural(slips, 'slip')} logged, and the card is updated."

    def teach_command(self, cmd: str) -> None:
        s = self.core.state
        st = self._current()
        if st is None:
            return
        steps = self._steps()
        if cmd == "teach_repeat":
            self._say_step()
        elif cmd == "teach_back":
            if s.teach_index == 0:
                self.core.say("This is the first step.")
            else:
                self._move_to(s.teach_index - 1)
            self._say_step()
        elif cmd == "teach_skip":
            ops.run(self.core, "log_trainee", {"step": st["id"], "kind": "mistake", "detail": self._slip(st)})
            self.core.say(f"Okay, skipping {self._title(st)}. I've logged it.")
            self._move_to(s.teach_index + 1)
            self._say_step()
        elif cmd == "teach_left":
            left = len(steps) - s.teach_index
            self.core.say("This is the last step." if left == 1 else f"{_plural(left, 'step')} left, counting this one.")
        elif cmd == "teach_whats_next":
            if s.teach_index + 1 >= len(steps):
                self.core.say(f"After {self._title(st)}, you're finished. This is the last step.")
            else:
                nxt = steps[s.teach_index + 1]
                self.core.say(f"After this comes step {nxt['n']}: {self._title(nxt)}. Finish this one first.")

    @staticmethod
    def _title(st: dict) -> str:
        return str(st.get("title") or st["id"]).rstrip(".").lower()

    def _slip(self, st: dict) -> str:
        return f"skipping {self._title(st)}"

    def _look(self, st: dict):
        if not self.core.eyes.recent(10):
            return None
        cue = st.get("cue") or st.get("action") or st.get("title")
        try:
            return self.core.eyes.look(LOOK_PROMPT.format(title=st.get("title"), cue=str(cue).rstrip(".")), 10).get("yes")
        except Exception as e:
            self.core.note("warn", f"look failed: {e}")
            return None

    def teach_check(self) -> None:
        st = self._current()
        if st is None:
            return
        verdict = self._look(st)
        retried = getattr(self, "_teach_retry", None) == st["id"]
        if verdict is None and not retried:
            self._teach_retry = st["id"]
            self.core.say("I can't quite tell from here. Hold it up to the camera and say done again.")
            return
        self._teach_retry = None
        if verdict is False:
            whys = sorted(st["whys"], key=lambda w: w.get("source") != "expert")
            why = _sentence(whys[0]["why"]) if whys else ""
            ops.run(self.core, "log_trainee", {"step": st["id"], "kind": "mistake", "detail": self._slip(st)})
            reason = f" {self.cfg.expert_name}'s reason: {why}" if why else ""
            title = self._title(st)
            self.core.say(f"Hold on, {title[:1].lower() + title[1:]} isn't done yet.{reason}")
            return
        if verdict is None:
            self.core.note("warn", f"accepted {st['id']} without a camera check")
            self.core.say("I still can't see it, so I'll take your word for it and note that.")
        ops.run(self.core, "log_trainee", {"step": st["id"], "kind": "done"})
        self._say_step()
