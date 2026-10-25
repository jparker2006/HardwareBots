"""Demo-safe mode: seed a known-good card from a recording with no model call, and reset to a clean data folder.

A recording may carry card.json ({"procedure": ..., "steps": [{title, action, cue, why, consequence, frame}]}).
Without one, steps come straight from the expert's capture utterances: no Gemma, same result every time.
A step's frame (a JPEG path relative to the recording) becomes its photo; from a script, the first frame after the
utterance, else the last one before it."""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from .conductor import COMMANDS
from .recorder import load_script

PATTERNS = [(name, re.compile(rx, re.I)) for name, rx in COMMANDS]
FILLER = re.compile(r"^\W*(?:(?:first|then|now|next|and|okay|ok|alright)\b[\s,]*)*(?:i\s+)?", re.I)
REASON = re.compile(r"^\W*(so|because|otherwise|that way)\b", re.I)


class SeedError(Exception):
    pass


def command(text: str) -> str | None:
    for name, rx in PATTERNS:
        if rx.search(text):
            return name
    return None


def card_from_script(root: Path) -> dict:
    lines = load_script(root)
    meta_path = Path(root) / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    said, frames = [], []
    for x in lines:
        if x.get("heard") or x.get("text"):
            said.append([x.get("heard") or x.get("text"), frames[-1] if frames else None, None])
        elif x.get("frame"):
            frames.append(str(Path(root) / x["frame"]))
            if said and not said[-1][2]:
                said[-1][2] = frames[-1]
    if any(command(t) == "start_capture" for t, _, _ in said):
        said = said[[command(t) for t, _, _ in said].index("start_capture") + 1:]
    steps: list[dict] = []
    for text, before, after in said:
        cmd = command(text)
        if cmd in ("end", "start_teach"):
            break
        if cmd:
            continue
        if REASON.match(text) and steps and not steps[-1].get("why"):
            steps[-1]["why"] = text.strip()
            continue
        action = FILLER.sub("", text).strip()
        action = action[:1].upper() + action[1:]
        steps.append({"title": action.split(",")[0].strip(), "action": action})
        if after or before:
            steps[-1]["frame"] = after or before
    return {"procedure": meta.get("procedure"), "steps": steps}


def load_card(path: Path) -> dict:
    path = Path(path)
    card_path = path if path.is_file() else path / "card.json"
    if card_path.exists():
        card = json.loads(card_path.read_text())
        for st in card.get("steps", []):
            if st.get("frame"):
                st["frame"] = str(card_path.parent / st["frame"])
        return card
    if (path / "script.jsonl").exists():
        return card_from_script(path)
    raise SeedError(f"no card.json or script.jsonl in {path}")


def seed(run, card: dict, confirm: bool = True, add_frame=None) -> dict:
    """run(op_name, args) calls one operation, in-process or over HTTP; add_frame(jpeg) -> frame_id stores a step photo.
    Refuses to stack on an existing card."""
    proc = run("get_procedure", {"procedure": card.get("procedure")})
    name = proc["procedure"]
    if proc["steps"]:
        raise SeedError(f"{name} already has {len(proc['steps'])} steps. Run `jm reset` first.")
    if not card.get("steps"):
        raise SeedError("no steps found to seed")
    whys = frames = 0
    for st in card["steps"]:
        frame_id = add_frame(Path(st["frame"]).read_bytes()) if add_frame and st.get("frame") else None
        frames += bool(frame_id)
        step_id = run("save_step", {"title": st["title"], "action": st.get("action") or st["title"],
                                    "cue": st.get("cue"), "frame_id": frame_id, "procedure": name,
                                    "source": "expert"})["step_id"]
        if st.get("why"):
            run("save_why", {"step": step_id, "why": st["why"], "consequence": st.get("consequence")})
            whys += 1
        if confirm:
            run("confirm", {"step": step_id})
    return {"procedure": name, "steps": len(card["steps"]), "whys": whys, "frames": frames, "confirmed": confirm}


def reset(data_dir: Path) -> Path | None:
    """Moves everything in the data folder to <data>/archive/<timestamp>/. Returns that path, or None if already clean."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    items = [p for p in data_dir.iterdir() if p.name != "archive"]
    if not items:
        return None
    base = dest = data_dir / "archive" / time.strftime("%Y%m%d-%H%M%S")
    n = 2
    while dest.exists():
        dest, n = base.with_name(f"{base.name}-{n}"), n + 1
    dest.mkdir(parents=True)
    for p in items:
        shutil.move(str(p), str(dest / p.name))
    return dest
