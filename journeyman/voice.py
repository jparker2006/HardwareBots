"""What Journeyman is doing right now (listening, hearing, thinking, speaking), plus a filler line if thinking drags."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

STATES = ("listening", "hearing", "thinking", "speaking")
FILLERS = ("One moment.", "Let me think about that.", "Give me a second.", "Still working on it.")
HEARING_STALE_S = 20.0


class Voice:
    """One turn = one utterance handled by the conductor. At most one filler line per turn."""

    def __init__(self, core):
        self.core = core
        self.filler_after = core.config.filler_after_s
        self._lock = threading.RLock()
        self.in_turn = False
        self.phase: str | None = None
        self.thinking_since: float | None = None
        self.filler_used = False
        self.heard_at: float | None = None
        self.fillers = 0

    @property
    def state(self) -> str:
        return self.core.state.voice_state

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            time.sleep(0.25)
            try:
                self.poll()
            except Exception as e:
                self.core.note("error", f"voice: {e}")

    def _set(self, state: str) -> None:
        if state != self.core.state.voice_state:
            self.core.emit("voice_state", state=state)

    # ---- inputs -------------------------------------------------------
    @contextmanager
    def turn(self):
        with self._lock:
            self.in_turn, self.phase, self.thinking_since, self.filler_used, self.heard_at = True, None, None, False, None
        try:
            yield
        finally:
            with self._lock:
                self.in_turn, self.phase, self.thinking_since = False, None, None
                self.settle()

    def hearing(self, now: float | None = None) -> None:
        with self._lock:
            if self.in_turn:
                self.phase = "hearing"
            self.settle(now)

    def heard(self, now: float | None = None) -> None:
        """The headset says speech has started; shown until that utterance arrives (or goes stale)."""
        with self._lock:
            self.heard_at = now or time.time()
            self.settle(now)

    def thinking(self, now: float | None = None) -> None:
        with self._lock:
            if not self.in_turn:
                return
            self.phase = "thinking"
            if self.thinking_since is None and not self.filler_used:
                self.thinking_since = now or time.time()
            self.settle(now)

    def spoke(self) -> None:
        with self._lock:
            self.thinking_since = None
            self.filler_used = True
            self._set("speaking")

    # ---- clock --------------------------------------------------------
    def poll(self, now: float | None = None) -> None:
        now = now or time.time()
        with self._lock:
            if self.in_turn and self.thinking_since is not None and now - self.thinking_since >= self.filler_after:
                self.thinking_since = None
                self.core.say(FILLERS[self.fillers % len(FILLERS)])
                self.fillers += 1
            self.settle(now)

    def settle(self, now: float | None = None) -> None:
        now = now or time.time()
        with self._lock:
            if self.heard_at is not None and now - self.heard_at > HEARING_STALE_S:
                self.heard_at = None
            if self.core.speaking(now):
                self._set("speaking")
            elif self.in_turn and self.phase:
                self._set(self.phase)
            elif self.heard_at is not None:
                self._set("hearing")
            else:
                self._set("listening")
