"""The one operation set. CLI commands, HTTP calls and Gemma tools all come from this registry."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .state import slug


@dataclass
class Param:
    name: str
    type: str = "string"
    help: str = ""
    required: bool = True
    enum: list[str] | None = None
    default: Any = None


@dataclass
class Op:
    name: str
    kind: str
    help: str
    handler: Callable[..., Any]
    params: list[Param] = field(default_factory=list)
    agent: bool = False

    def tool_schema(self) -> dict:
        props, required = {}, []
        for p in self.params:
            spec: dict = {"type": p.type, "description": p.help}
            if p.enum:
                spec["enum"] = p.enum
            props[p.name] = spec
            if p.required:
                required.append(p.name)
        return {"type": "function", "function": {"name": self.name, "description": self.help,
                                                 "parameters": {"type": "object", "properties": props,
                                                                "required": required}}}


REGISTRY: dict[str, Op] = {}


def op(name: str, kind: str, help: str, params: list[Param] | None = None, agent: bool = False):
    def wrap(fn):
        REGISTRY[name] = Op(name, kind, help, fn, params or [], agent)
        return fn
    return wrap


def run(core, name: str, args: dict | None = None, caller: str = "cli") -> Any:
    o = REGISTRY.get(name)
    if o is None:
        raise KeyError(f"unknown operation {name}")
    if caller == "agent" and not o.agent:
        raise PermissionError(f"{name} is not available to the agent")
    args = dict(args or {})
    clean = {}
    for p in o.params:
        if p.name in args and args[p.name] is not None:
            value = args[p.name]
            if p.type == "number":
                value = float(value)
            elif p.type == "integer":
                value = int(value)
            if p.enum and value not in p.enum:
                raise ValueError(f"{p.name} must be one of {p.enum}")
            clean[p.name] = value
        elif p.required:
            raise ValueError(f"missing {p.name}")
        elif p.default is not None:
            clean[p.name] = p.default
    return o.handler(core, **clean)


def tool_schemas() -> list[dict]:
    return [o.tool_schema() for o in REGISTRY.values() if o.agent]


# ---- helpers -----------------------------------------------------------

def resolve_step(core, ref: str) -> dict:
    """Accepts '3', 'cable-swap/3', '/procedures/cable-swap/3' and an optional '@rN'."""
    ref = str(ref).strip()
    rev = None
    m = re.search(r"@r(\d+)$", ref)
    if m:
        rev, ref = int(m.group(1)), ref[: m.start()]
    ref = re.sub(r"^/procedures/", "", ref)
    if "/" not in ref:
        ref = f"{slug(core.state.procedure or core.config.default_procedure)}/{ref}"
    step = core.state.steps.get(ref)
    if step is None:
        raise KeyError(f"no step {ref}")
    if rev is not None and rev != step["rev"]:
        raise ValueError(f"changed since you looked: {ref} is at r{step['rev']}, you saw r{rev}. Re-review.")
    return step


STOPWORDS = {"the", "and", "for", "with", "that", "this", "then", "from", "into", "its", "your", "you", "are",
              "was", "but", "not", "did", "does", "why", "what", "how", "should", "they", "them", "their"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOPWORDS}


def similar(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def _step_view(step: dict) -> dict:
    return {"path": f"/procedures/{step['id']}@r{step['rev']}", "n": step["n"], "title": step.get("title"),
            "action": step.get("action"), "cue": step.get("cue"), "whys": step["whys"],
            "warnings": step["warnings"], "confirmed": step["confirmed"], "frame_id": step.get("frame_id")}


# ---- list and inspect ---------------------------------------------------

@op("status", "list", "Mode, session, procedure and counts.")
def status(core):
    s = core.state
    return {"mode": s.mode, "session": s.session_id, "procedure": s.procedure,
            "procedures": {n: len(p["steps"]) for n, p in s.procedures.items()},
            "open_questions": len(s.open_questions), "questions_asked": len(s.questions), "pending_question": s.pending_question,
            "camera": core.eyes.status(), "seq": s.last_seq,
            "hero": s.hero, "observed": s.observed, "voice_state": getattr(s, "voice_state", None)}


@op("ls", "list", "List what exists under a path.", [Param("path", help="e.g. /procedures", required=False, default="/")])
def ls(core, path="/"):
    s = core.state
    path = path.rstrip("/") or "/"
    if path == "/":
        return ["/procedures", "/sessions", "/questions/open", "/lessons"]
    if path == "/procedures":
        return [f"/procedures/{slug(n)} ({len(p['steps'])} steps, r{p['rev']})" for n, p in s.procedures.items()]
    if path.startswith("/procedures/"):
        return [f"/procedures/{st['id']}@r{st['rev']}  {st.get('title')}" for st in s.procedure_steps(path.split("/")[2])]
    if path == "/sessions":
        return [f"/sessions/{i} {x['kind']} {x['procedure']}{' (live)' if x['ended'] is None else ''}" for i, x in s.sessions.items()]
    if path == "/questions/open":
        return [q["question"] for q in s.open_questions.values()]
    if path == "/lessons":
        return [x["text"] for x in s.lessons]
    raise KeyError(f"nothing at {path}")


@op("show", "inspect", "Show one step or session.", [Param("path", help="/procedures/<p>/<n> or /sessions/<id>")])
def show(core, path):
    if path.startswith("/sessions/"):
        return core.state.sessions[path.split("/")[2]]
    return _step_view(resolve_step(core, path))


@op("card", "inspect", "The procedure card as Markdown.", [Param("procedure", required=False)])
def card(core, procedure=None):
    return core.state.card_markdown(procedure or core.state.procedure or core.config.default_procedure)


@op("search_knowledge", "inspect", "Search saved steps, reasons and warnings. Use before answering any question.",
    [Param("query", help="what the person asked about")], agent=True)
def search_knowledge(core, query):
    scored = []
    for step in core.state.steps.values():
        text = " ".join([step.get("title") or "", step.get("action") or "", step.get("cue") or ""]
                        + [w["why"] + " " + w.get("consequence", "") for w in step["whys"]] + step["warnings"])
        score = len(_words(query) & _words(text))
        if score:
            scored.append((score, step))
    scored.sort(key=lambda x: -x[0])
    return {"matches": [_step_view(st) for _, st in scored[:3]],
            "note": "Answer only from these matches. If none fits, say it is not captured and call log_question."}


@op("get_procedure", "inspect", "Ordered steps of a procedure.", [Param("procedure", required=False)], agent=True)
def get_procedure(core, procedure=None):
    name = procedure or core.state.procedure or core.config.default_procedure
    return {"procedure": name, "steps": [_step_view(st) for st in core.state.procedure_steps(name)],
            "current_step": core.state.teach_index + 1 if core.state.mode == "teach" else None}


# ---- sense and speak ----------------------------------------------------

@op("look", "sense", "Ask the camera about the last few seconds of work.",
    [Param("question", help="a specific visual question"), Param("seconds", "number", "how far back", False, default=10)],
    agent=True)
def look(core, question, seconds=10):
    return core.eyes.look(question, seconds)


@op("speak", "speak", "Say something out loud to the person. Keep it to one or two short sentences.",
    [Param("text")], agent=True)
def speak(core, text):
    return {"say_id": core.say(text)}


# ---- change -------------------------------------------------------------

@op("save_step", "change", "Record a step of the procedure being captured.",
    [Param("title", help="short imperative title"), Param("action", help="what was done"),
     Param("cue", help="what to look for", required=False), Param("frame_id", required=False),
     Param("procedure", required=False), Param("source", required=False, enum=["observed", "expert", "inferred"], default="observed")],
    agent=True)
def save_step(core, title, action, cue=None, frame_id=None, procedure=None, source="observed"):
    with core.lock:
        name = procedure or core.state.procedure or core.config.default_procedure
        for st in core.state.procedure_steps(name):
            if similar(st.get("title", ""), title) >= 0.6:
                return {"step_id": st["id"], "duplicate": True}
        n = len(core.state.procedure_steps(name)) + 1
        step_id = f"{slug(name)}/{n}"
        core.emit("step_saved", step_id=step_id, procedure=name, title=title, action=action, cue=cue,
                  frame_id=frame_id, source=source)
    return {"step_id": step_id}


@op("save_why", "change", "Attach the reason for a step and what goes wrong if it is skipped.",
    [Param("step", help="step number or path"), Param("why"), Param("consequence", required=False),
     Param("source", required=False, enum=["expert", "inferred"], default="expert")], agent=True)
def save_why(core, step, why, consequence=None, source="expert"):
    st = resolve_step(core, step)
    core.emit("why_saved", step_id=st["id"], why=why, consequence=consequence or "", source=source)
    return {"step_id": st["id"]}


@op("log_question", "change", "Queue a question nobody has answered yet for the expert's next session.",
    [Param("question"), Param("asked_by", required=False, default="trainee")], agent=True)
def log_question(core, question, asked_by="trainee"):
    question = re.sub(r"^\s*(hey\s+)?journeyman[\s,:.!]*", "", question, flags=re.I).strip()
    question = question[:1].upper() + question[1:]
    for q in core.state.open_questions.values():
        if similar(q["question"], question) >= 0.8:
            return {"question_id": q["question_id"], "duplicate": True}
    qid = uuid.uuid4().hex[:8]
    core.emit("question_logged", question_id=qid, question=question, asked_by=asked_by,
              procedure=core.state.procedure or core.config.default_procedure)
    return {"question_id": qid}


@op("flag_disagreement", "change", "Two experts did a step differently.",
    [Param("step"), Param("a", help="first way"), Param("b", help="second way")], agent=True)
def flag_disagreement(core, step, a, b):
    st = resolve_step(core, step)
    core.emit("disagreement_flagged", step_id=st["id"], a=a, b=b)
    return {"step_id": st["id"]}


@op("log_trainee", "change", "Record a trainee finishing a step or making a mistake.",
    [Param("step"), Param("kind", enum=["done", "mistake"]), Param("detail", required=False, default="")], agent=True)
def log_trainee(core, step, kind, detail=""):
    st = resolve_step(core, step)
    sid = core.state.session_id or ""
    if kind == "done":
        core.emit("trainee_step_done", session_id=sid, step_id=st["id"])
    else:
        core.emit("trainee_mistake", session_id=sid, step_id=st["id"], detail=detail or "incomplete")
    return {"step_id": st["id"], "kind": kind}


@op("save_lesson", "change", "A note to yourself that will make the next session better.", [Param("text")], agent=True)
def save_lesson(core, text):
    core.emit("lesson_saved", text=text)
    return {"ok": True}


@op("confirm", "change", "Mark a step and its reasons as confirmed by the expert.", [Param("step", help="path, optionally @rN")])
def confirm(core, step):
    st = resolve_step(core, step)
    core.emit("item_confirmed", step_id=st["id"])
    return {"step_id": st["id"]}


@op("correct", "change", "Correct a field of a step (expert only).",
    [Param("step"), Param("field", enum=["title", "action", "cue"]), Param("value")])
def correct(core, step, field, value):
    st = resolve_step(core, step)
    core.emit("item_corrected", step_id=st["id"], field=field, value=value)
    return {"step_id": st["id"]}


# ---- sessions and input -------------------------------------------------

@op("session_start", "session", "Start a capture (expert) or teach (new hire) session.",
    [Param("kind", enum=["capture", "teach"]), Param("procedure", required=False), Param("trainee", required=False)])
def session_start(core, kind, procedure=None, trainee=None):
    return {"session_id": core.conductor.start_session(kind, procedure, trainee)}


@op("session_end", "session", "End the current session.")
def session_end(core):
    return {"ended": core.conductor.end_session()}


@op("hear", "session", "Inject an utterance as text, as if it was spoken.", [Param("text")])
def hear(core, text):
    core.conductor.submit_text(text)
    return {"queued": True}
