"""Wires the pieces together. journeymand owns state; everything else is a client."""
from __future__ import annotations

import json
import threading
import time
import uuid

from .agent import Agent
from .conductor import Conductor
from .config import Config
from .ears import Ears
from .eyes import Eyes
from .gemma import Gemma
from .journal import Journal
from .local_http import check_url
from .state import State
from .voice import Voice

CARD_EVENTS = {"step_saved", "why_saved", "item_confirmed", "item_corrected", "question_logged",
               "trainee_mistake", "disagreement_flagged"}


class Core:
    def __init__(self, config: Config | None = None, sync: bool = False, gemma: Gemma | None = None,
                 vision: Gemma | None = None):
        self.config = config or Config()
        check_url(self.config.gemma_url)
        check_url(self.config.vision_url)
        self.lock = threading.RLock()
        self.state = State()
        self.journal = Journal(self.config.journal_path)
        for event in self.journal.read():
            self.state.apply(event)
        self.state.voice_state = "listening"
        self.listeners: list = []
        self.journal.subscribe(self._on_event)
        self.gemma = gemma or Gemma(self.config.gemma_url, self.config.gemma_model, self.config)
        self.vision = vision or (self.gemma if self.config.vision_url == self.config.gemma_url
                                 and self.config.vision_model == self.config.gemma_model
                                 else Gemma(self.config.vision_url, self.config.vision_model, self.config))
        self.eyes = Eyes(self)
        self.ears = Ears(self)
        self.agent = Agent(self)
        self.conductor = Conductor(self, sync=sync)
        self.outbox: list[dict] = []
        self.outbox_cv = threading.Condition()
        self._speaking_until = 0.0
        self.voice = Voice(self)

    def start(self) -> None:
        self.eyes.start()
        self.conductor.start()
        self.voice.start()

    # ---- events ---------------------------------------------------------
    def emit(self, type_: str, **data) -> dict:
        with self.lock:
            return self.journal.append(type_, **data)

    def _on_event(self, event: dict) -> None:
        self.state.apply(event)
        if event["type"] in CARD_EVENTS:
            self.state.export_cards(self.config.cards_dir)
        for fn in list(self.listeners):
            try:
                fn(event)
            except Exception:
                self.listeners.remove(fn)

    def note(self, level: str, text: str) -> None:
        self.emit("note", level=level, text=text)

    # ---- speech out -----------------------------------------------------
    def say(self, text: str) -> str:
        say_id = uuid.uuid4().hex[:8]
        self.emit("said", say_id=say_id, text=text)
        self._speaking_until = time.time() + 1.0 + 0.07 * len(text)
        self.voice.spoke()
        with self.outbox_cv:
            self.outbox.append({"id": say_id, "seq": self.state.last_seq, "text": text})
            self.outbox = self.outbox[-50:]
            self.outbox_cv.notify_all()
        return say_id

    def spoken(self, say_id: str) -> None:
        if self.outbox and self.outbox[-1]["id"] == say_id:
            self._speaking_until = time.time() + 0.3

    def speaking(self, now: float | None = None) -> bool:
        return (now or time.time()) < self._speaking_until

    def wait_outbox(self, after_seq: int, timeout: float = 20.0) -> list[dict]:
        with self.outbox_cv:
            items = [x for x in self.outbox if x["seq"] > after_seq]
            if not items:
                self.outbox_cv.wait(timeout)
                items = [x for x in self.outbox if x["seq"] > after_seq]
        return items

    def dump(self) -> str:
        return json.dumps(self.state.snapshot(), default=str)
