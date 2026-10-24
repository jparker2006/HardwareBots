"""Projections rebuilt from the journal. One apply() holds every state rule."""
from __future__ import annotations

import re
from pathlib import Path


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "untitled"


class State:
    def __init__(self) -> None:
        self.procedures: dict[str, dict] = {}
        self.steps: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.open_questions: dict[str, dict] = {}
        self.disagreements: list[dict] = []
        self.lessons: list[dict] = []
        self.frames: dict[str, dict] = {}
        self.questions: dict[str, dict] = {}
        self.gaps: list[dict] = []
        self.said: list[dict] = []
        self.notes: list[dict] = []
        self.mode = "idle"
        self.session_id: str | None = None
        self.procedure: str | None = None
        self.teach_index = 0
        self.pending_question: dict | None = None
        self.hero: dict | None = None
        self.observed: dict | None = None
        self._hero_kind: str | None = None
        self.last_seq = 0

    # ---- reads -------------------------------------------------------
    def procedure_steps(self, name: str) -> list[dict]:
        proc = self.procedures.get(name)
        return [self.steps[s] for s in proc["steps"]] if proc else []

    def session(self) -> dict | None:
        return self.sessions.get(self.session_id) if self.session_id else None

    def transcript(self, since: float = 0.0) -> list[dict]:
        s = self.session()
        return [u for u in (s["transcript"] if s else []) if u["t1"] >= since]

    def latest_frame_id(self) -> str | None:
        return next(reversed(self.frames), None) if self.frames else None

    def snapshot(self) -> dict:
        snap = {
            "seq": self.last_seq, "mode": self.mode, "session_id": self.session_id,
            "procedure": self.procedure, "teach_index": self.teach_index,
            "pending_question": self.pending_question,
            "procedures": {n: {**p, "steps": self.procedure_steps(n)} for n, p in self.procedures.items()},
            "sessions": self.sessions, "open_questions": list(self.open_questions.values()),
            "disagreements": self.disagreements, "lessons": self.lessons,
            "gaps": self.gaps[-20:], "said": self.said[-20:], "notes": self.notes[-20:],
            "frames": list(self.frames.values())[-12:],
        }
        snap.update(hero=self.hero, observed=self.observed, voice_state=getattr(self, "voice_state", None))
        return snap

    # ---- the one write path -------------------------------------------
    def apply(self, event: dict) -> None:
        self.last_seq = event["seq"]
        t, d, kind = event["t"], event["data"], event["type"]
        if kind == "session_started":
            self.sessions[d["session_id"]] = {
                "id": d["session_id"], "kind": d["kind"], "procedure": d["procedure"],
                "started": t, "ended": None, "transcript": [], "trainee": d.get("trainee"),
                "done_steps": [], "mistakes": [],
            }
            self.mode, self.session_id, self.procedure = d["kind"], d["session_id"], d["procedure"]
            self.teach_index, self.pending_question = 0, None
            self.hero, self.observed, self._hero_kind = None, None, None
            self._ensure_procedure(d["procedure"], t)
        elif kind == "session_ended":
            s = self.sessions.get(d["session_id"])
            if s and s["ended"] is None:
                s["ended"] = t
            if self.session_id == d["session_id"]:
                self.mode, self.session_id, self.pending_question = "idle", None, None
        elif kind == "utterance_heard":
            s = self.sessions.get(d.get("session_id") or "")
            if s:
                s["transcript"].append({"t0": d["t0"], "t1": d["t1"], "text": d["text"], "seq": event["seq"]})
        elif kind == "frame_kept":
            self.frames[d["frame_id"]] = {"id": d["frame_id"], "path": d["path"], "t": d.get("at", t)}
            if len(self.frames) > 600:
                pinned = {st.get("frame_id") for st in self.steps.values()}
                old = next((f for f in self.frames if f not in pinned), None)
                if old:
                    self.frames.pop(old)
        elif kind == "gap_noticed":
            self.gaps.append({**d, "t": t})
        elif kind == "question_asked":
            self.questions[d["question_id"]] = {**d, "t": t, "answer": None}
            self.pending_question = {**d, "t": t}
            self._hero_kind = "asking"
        elif kind == "answer_attached":
            q = self.questions.get(d["question_id"])
            if q:
                q["answer"] = d["text"]
            if self.pending_question and self.pending_question["question_id"] == d["question_id"]:
                self.pending_question = None
        elif kind == "step_saved":
            self._ensure_procedure(d["procedure"], t)
            step = self.steps.get(d["step_id"])
            if step is None:
                proc = self.procedures[d["procedure"]]
                step = {"id": d["step_id"], "procedure": d["procedure"], "n": len(proc["steps"]) + 1,
                        "whys": [], "warnings": [], "mistakes": 0, "rev": 0, "confirmed": False, "created": t}
                proc["steps"].append(d["step_id"])
                self.steps[d["step_id"]] = step
            for key in ("title", "action", "cue", "source"):
                if d.get(key) is not None:
                    step[key] = d[key]
            fid = d.get("frame_id")
            if self.frames and fid not in self.frames:
                fid = None if step.get("frame_id") in self.frames else self.latest_frame_id()
            if fid:
                step["frame_id"] = fid
            step["rev"] += 1
            self.procedures[d["procedure"]]["rev"] += 1
        elif kind == "why_saved":
            step = self.steps.get(d["step_id"])
            if step:
                step["whys"].append({"why": d["why"], "consequence": d.get("consequence", ""),
                                     "source": d.get("source", "expert"), "confirmed": d.get("source") == "expert",
                                     "t": t})
                step["rev"] += 1
        elif kind == "item_confirmed":
            step = self.steps.get(d["step_id"])
            if step:
                step["confirmed"] = True
                for w in step["whys"]:
                    w["confirmed"] = True
                step["rev"] += 1
        elif kind == "item_corrected":
            step = self.steps.get(d["step_id"])
            if step:
                step[d["field"]] = d["value"]
                step["confirmed"] = True
                step["rev"] += 1
        elif kind == "question_logged":
            self.open_questions[d["question_id"]] = {**d, "t": t, "status": "open"}
        elif kind == "disagreement_flagged":
            self.disagreements.append({**d, "t": t})
        elif kind == "trainee_step_done":
            s = self.sessions.get(d["session_id"])
            if s and d["step_id"] not in s["done_steps"]:
                s["done_steps"].append(d["step_id"])
            if self.session_id == d["session_id"]:
                self.teach_index += 1
        elif kind == "teach_moved":
            if self.session_id == d["session_id"]:
                self.teach_index = d["index"]
        elif kind == "trainee_mistake":
            s = self.sessions.get(d["session_id"])
            if s:
                s["mistakes"].append({"step_id": d["step_id"], "detail": d["detail"], "t": t})
            step = self.steps.get(d["step_id"])
            if step:
                step["mistakes"] += 1
                if d["detail"] not in step["warnings"]:
                    step["warnings"].append(d["detail"])
                step["rev"] += 1
            self._hero_kind = "correcting"
        elif kind == "lesson_saved":
            self.lessons.append({"text": d["text"], "t": t})
        elif kind == "said":
            self.said.append({"text": d["text"], "say_id": d["say_id"], "t": t})
            self.hero = {"text": d["text"], "kind": self._hero_kind or "instructing", "t": t}
            self._hero_kind = None
        elif kind == "observed":
            self.observed = {"text": d["text"], "frame_id": d.get("frame_id"), "source": d.get("source"), "t": t}
        elif kind == "note":
            self.notes.append({**d, "t": t})

    def _ensure_procedure(self, name: str, t: float) -> None:
        if name not in self.procedures:
            self.procedures[name] = {"name": name, "steps": [], "rev": 0, "created": t}

    # ---- export ------------------------------------------------------
    def card_markdown(self, name: str) -> str:
        lines = [f"# {name}", ""]
        for step in self.procedure_steps(name):
            mark = "" if step.get("confirmed") else " *(unconfirmed)*"
            lines.append(f"## {step['n']}. {step.get('title', step['id'])}{mark}")
            if step.get("action"):
                lines.append(f"- **Do:** {step['action']}")
            if step.get("cue"):
                lines.append(f"- **Look for:** {step['cue']}")
            for w in step["whys"]:
                tag = "" if w["confirmed"] else " *(unconfirmed)*"
                lines.append(f"- **Why:** {w['why']}{tag}")
                if w.get("consequence"):
                    lines.append(f"- **If skipped:** {w['consequence']}")
            for warn in step["warnings"]:
                lines.append(f"- **Common mistake:** {warn}")
            lines.append("")
        open_qs = [q for q in self.open_questions.values() if q.get("procedure") == name]
        if open_qs:
            lines.append("## Open questions for the expert")
            lines += [f"- {q['question']}" for q in open_qs]
            lines.append("")
        return "\n".join(lines)

    def export_cards(self, cards_dir: Path) -> None:
        cards_dir.mkdir(parents=True, exist_ok=True)
        for name in self.procedures:
            (cards_dir / f"{slug(name)}.md").write_text(self.card_markdown(name))
