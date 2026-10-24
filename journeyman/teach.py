"""Teach mode: give the steps by voice, check each one with the camera, log slips."""
from __future__ import annotations

from . import ops


class TeachMixin:
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
