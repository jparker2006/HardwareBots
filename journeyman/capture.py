"""Capture mode: watch the expert, record steps, ask about what they didn't explain, save the answers."""
from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

from . import ops
from .gemma import image_part, text_part

OBSERVE_PROMPT = """You watch a technician work. Compare what they DID in the frames with what they SAID.
Frames (oldest first): {frame_ids}
Said (seconds ago): {said}
Steps already recorded, do not repeat them: {known}
Reply with JSON only, this exact shape:
{{"new_steps": [{{"title": "<verb + object>", "action": "<what they did>", "cue": "<what done looks like>", "frame_id": "<frame id>", "narrated": true}}],
 "unexplained": [{{"action": "<what they did>", "frame_id": "<frame id>", "confidence": 0.8}}]}}
Rules:
- new_steps: only actions you can see that are not recorded yet. title: 2-6 words, starts with a verb.
- narrated: true if they said what they did.
- unexplained: a deliberate action they did but never said WHY. Skip it if they gave a reason.
- confidence: 0.0-1.0, how sure you are you saw it.
- Use [] when nothing is new."""

QUESTION_PROMPT = """You are learning a job from {expert}. They just did this without saying why: "{action}".
They recently said: {said}
Write one short, friendly spoken question asking why. Under 15 words. Output only the question."""

WHY_PROMPT = """You asked the expert: "{question}" (about step {default}).
They answered: "{answer}"
Recorded steps:
{steps}
Reply with JSON only: {{"step": <number of the step the answer is about>, "why": "<their reason, in their own words>", "consequence": "<what goes wrong if skipped, in their words, or empty>"}}
Use "why": "" if they did not give a reason."""

REASON_WORDS = re.compile(r"\b(because|cause|so that|so the|so it|so i|so you|otherwise|in case|to make sure|"
                          r"to avoid|to keep|to stop|to prevent|or else|that way|reason)\b")
CONTEXT_S = 30.0
GAP_TTL_S = 60.0
PENDING_TTL_S = 60.0
MAX_CANDIDATES = 3
MAX_QUESTIONS = 8


def imperative(text: str) -> str:
    """'The tech unplugs the cable.' -> 'Unplug the cable'."""
    t = re.sub(r"^(the )?(technician|tech|expert|user|person|worker|he|she|they)\s+", "", text.strip(), flags=re.I)
    t = t.strip().rstrip(".!").strip()
    words = t.split()
    if not words:
        return ""
    w = words[0].lower()
    if w in ("does", "goes"):
        w = w[:-2]
    elif re.search(r"(sh|ch|x|ss|z)es$", w):
        w = w[:-2]
    elif w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("s") and not re.search(r"(ss|us|is)$", w):
        w = w[:-1]
    return " ".join([w.capitalize()] + words[1:])[:80]


def explained(action: str, utterances: list[str]) -> bool:
    """True when the expert already gave a reason for something like `action`."""
    words = ops._words(action)
    if not words:
        return False
    for u in utterances:
        if REASON_WORDS.search(u.lower()) and len(words & ops._words(u)) / len(words) >= 0.34:
            return True
    return False


def _conf(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return {"high": 0.9, "medium": 0.6, "low": 0.3}.get(str(value).strip().lower(), 0.0)


def _str(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse_observation(result: dict, frame_ids: list[str]) -> tuple[list[dict], list[dict]]:
    """Clean up whatever the model returned into (steps, gaps); drops malformed items and unknown frame ids."""
    def frame(v):
        v = _str(v)
        return v if v in frame_ids else None

    def items(key):
        v = result.get(key) if isinstance(result, dict) else None
        if isinstance(v, dict):
            v = [v]
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []

    steps = []
    for st in items("new_steps")[:3]:
        title, action = imperative(_str(st.get("title")) or _str(st.get("action"))), _str(st.get("action"))
        if title and action:
            narrated = st.get("narrated")
            steps.append({"title": title, "action": action, "cue": _str(st.get("cue")) or None,
                          "frame_id": frame(st.get("frame_id")),
                          "narrated": narrated is True or str(narrated).lower() == "true"})
    gaps = []
    for g in items("unexplained")[:2]:
        action = _str(g.get("action"))
        if action:
            gaps.append({"action": action, "frame_id": frame(g.get("frame_id")), "confidence": _conf(g.get("confidence"))})
    return steps, gaps


class CaptureMixin:
    clock = staticmethod(time.time)

    def _steps(self) -> list[dict]:
        return self.core.state.procedure_steps(self.core.state.procedure or "")

    def _said(self, since: float) -> list[tuple[float, str]]:
        return [(u["t1"], u["text"]) for u in self.core.state.transcript(since)]

    def _frame_parts(self, frames: list[dict], now: float) -> list[dict]:
        parts = []
        for f in frames:
            try:
                data = Path(f["path"]).read_bytes()
            except OSError:
                continue
            parts += [text_part(f"Frame {f['id']} ({now - f['t']:.0f}s ago):"), image_part(data)]
        return parts

    def observe(self, force: bool = False) -> dict | None:
        if not self._observing.acquire(blocking=force):
            return None
        try:
            s, now = self.core.state, self.clock()
            since = self.last_observe_at - 2
            self.last_observe_at, self.observed_seq = now, s.last_seq
            frames = [f for f in s.frames.values() if f["t"] >= since]
            if len(frames) > 6:
                frames = [frames[int(i * len(frames) / 5.0)] for i in range(5)] + [frames[-1]]
            if not frames:
                return None
            said = self._said(min(since, now - CONTEXT_S))
            said_text = " / ".join(f"[{now - t:.0f}s] {x}" for t, x in said) or "(nothing)"
            known = "; ".join(f"{st['n']}. {st.get('title', '')}" for st in self._steps()) or "(none)"
            parts = self._frame_parts(frames, now)
            ids = [f["id"] for f in frames]
            content = parts + [text_part(OBSERVE_PROMPT.format(frame_ids=", ".join(ids), said=said_text, known=known))]
            result, secs = self.core.vision.ask_json([{"role": "user", "content": content}], max_tokens=500)
            steps, gaps = parse_observation(result, ids)
            for item in steps + gaps:
                item["frame_id"] = item["frame_id"] or ids[-1]
            seen = [x["action"] for x in steps + gaps]
            if seen:
                self.core.emit("observed", text="; ".join(seen), frame_id=(steps + gaps)[0]["frame_id"], source="capture")
            for st in steps:
                if any(ops.similar(st["action"], k.get("action", "")) >= 0.6 for k in self._steps()):
                    continue
                ops.run(self.core, "save_step", {"title": st["title"], "action": st["action"], "cue": st["cue"],
                                                 "frame_id": st["frame_id"], "source": "observed"})
            for gap in gaps:
                why_not = self._skip_gap(gap, [x for _, x in said])
                if why_not:
                    self.core.note("info", f"gap skipped ({why_not}): {gap['action']}")
                    continue
                self.core.emit("gap_noticed", action=gap["action"], frame_id=gap["frame_id"], confidence=gap["confidence"])
                self.candidates.append({**gap, "at": now})
                self.candidates = self.candidates[-MAX_CANDIDATES:]
            self.core.note("info", f"observed {len(frames)} frames in {secs:.1f}s: "
                                   f"{len(steps)} steps, {len(gaps)} gaps")
            return result
        except Exception as e:
            self.core.note("error", f"observe: {e}")
            return None
        finally:
            self._observing.release()

    def _skip_gap(self, gap: dict, said: list[str]) -> str | None:
        action = gap["action"]
        if gap["confidence"] < self.cfg.gap_confidence:
            return f"confidence {gap['confidence']:.2f}"
        if any(ops.similar(action, a) >= 0.5 for a in self.asked_actions + [c["action"] for c in self.candidates]):
            return "already asked"
        if explained(action, said):
            return "already explained"
        step = self._match_step(action)
        if step and step["whys"]:
            return "step already has a reason"
        return None

    def _match_step(self, action: str) -> dict | None:
        steps = self._steps()
        if not steps:
            return None
        score = lambda st: ops.similar(st.get("action", "") + " " + st.get("title", ""), action)  # noqa: E731
        best = max(steps, key=score)
        return best if score(best) >= 0.2 else None

    def maybe_ask(self, now: float) -> bool:
        s = self.core.state
        self.candidates = [c for c in self.candidates if now - c["at"] <= GAP_TTL_S]
        pending = s.pending_question
        if not self.candidates or self.core.speaking(now) or len(self.asked_actions) >= MAX_QUESTIONS:
            return False
        if pending and now - pending.get("at", pending["t"]) < PENDING_TTL_S:
            return False
        if now - self.last_question_at < self.cfg.question_gap_s or now - self.last_utterance_end < self.cfg.silence_gate_s:
            return False
        gap = self.candidates.pop(0)
        said = [x for _, x in self._said(gap["at"] - CONTEXT_S)]
        if explained(gap["action"], said):
            self.core.note("info", f"gap skipped (explained before asking): {gap['action']}")
            return False
        step = self._step_for(gap)
        if step["whys"]:
            return False
        question = self._question(gap["action"], said)
        qid = uuid.uuid4().hex[:8]
        self.asked_actions.append(gap["action"])
        self.last_question_at = now
        self.core.emit("question_asked", question_id=qid, text=question, about=gap["action"],
                       step_id=step["id"], at=now)
        self.core.say(question)
        return True

    def _question(self, action: str, said: list[str]) -> str:
        try:
            msg, _ = self.core.gemma.chat([{"role": "user", "content": QUESTION_PROMPT.format(
                expert=self.cfg.expert_name, action=action, said=" / ".join(said[-3:]) or "(nothing)")}],
                max_tokens=40, temperature=0.4)
            lines = [x for x in (msg.get("content") or "").strip().splitlines() if x.strip()]
            question = lines[0].strip().strip('"*').strip() if lines else ""
        except Exception:
            question = ""
        if not question or len(question.split()) > 25:
            return f"Quick one: why {imperative(action).lower()}?"
        return question if question.endswith("?") else question.rstrip(".") + "?"

    def _step_for(self, gap: dict) -> dict:
        best = self._match_step(gap["action"])
        if best:
            return best
        ops.run(self.core, "save_step", {"title": imperative(gap["action"])[:60], "action": gap["action"],
                                         "frame_id": gap.get("frame_id"), "source": "observed"})
        return self._match_step(gap["action"]) or self._steps()[-1]

    def attach_answer(self, text: str) -> None:
        q = self.core.state.pending_question
        steps = self._steps()
        default = self.core.state.steps.get(q.get("step_id") or "")
        listing = "\n".join(f"{st['n']}. {st.get('title', '')}: {st.get('action', '')}" for st in steps[-8:]) or "(none)"
        try:
            parsed, _ = self.core.gemma.ask_json([{"role": "user", "content": WHY_PROMPT.format(
                question=q["text"], default=default["n"] if default else "?", answer=text, steps=listing)}],
                max_tokens=150)
        except Exception:
            parsed = {}
        if "why" in parsed and not _str(parsed.get("why")):
            self.core.note("info", f"not an answer, still waiting: {text}")
            return
        step = default
        try:
            step = next(st for st in steps if st["n"] == int(parsed.get("step")))
        except (StopIteration, TypeError, ValueError):
            pass
        self.core.emit("answer_attached", question_id=q["question_id"], step_id=step["id"] if step else None, text=text)
        if not step:
            return
        ops.run(self.core, "save_why", {"step": step["id"], "why": _str(parsed.get("why")) or text,
                                        "consequence": _str(parsed.get("consequence")), "source": "expert"})
        self.core.say("Got it, saved.")
