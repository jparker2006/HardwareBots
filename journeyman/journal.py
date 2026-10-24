"""Append-only event journal. Every fact is an event; state is rebuilt from it."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

EVENT_TYPES = {
    "session_started", "session_ended", "utterance_heard", "frame_kept", "gap_noticed",
    "question_asked", "answer_attached", "step_saved", "why_saved", "item_confirmed",
    "item_corrected", "question_logged", "disagreement_flagged", "trainee_step_done",
    "trainee_mistake", "lesson_saved", "said", "note",
}


class Journal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._subscribers: list[Callable[[dict], None]] = []
        self.seq = 0
        for event in self.read():
            self.seq = max(self.seq, event["seq"])

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        events = []
        with self.path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def subscribe(self, fn: Callable[[dict], None]) -> None:
        self._subscribers.append(fn)

    def append(self, type_: str, **data) -> dict:
        if type_ not in EVENT_TYPES:
            raise ValueError(f"unknown event type {type_}")
        with self._lock:
            self.seq += 1
            event = {"seq": self.seq, "t": round(time.time(), 3), "type": type_, "data": data}
            with self.path.open("a") as f:
                f.write(json.dumps(event) + "\n")
                f.flush()
                os.fsync(f.fileno())
        for fn in list(self._subscribers):
            fn(event)
        return event
