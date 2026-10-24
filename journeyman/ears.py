"""Speech to text with Gemma's audio input."""
from __future__ import annotations

import time
import uuid

from .gemma import GemmaError, audio_part, text_part, strip_control

DEAF = "Sorry, I couldn't hear that. My speech model isn't answering."
NO_AUDIO = "Sorry, this model can't take audio. Type it on the monitor, or switch to a model that hears."
SAY_EVERY_S = 30.0


class Ears:
    def __init__(self, core):
        self.core = core
        self._said_failure_at = 0.0

    def transcribe(self, wav: bytes) -> tuple[str, float, str]:
        clip_id = uuid.uuid4().hex[:8]
        path = self.core.config.audio_dir / f"{clip_id}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(wav)
        start = time.time()
        try:
            msg, _ = self.core.gemma.chat([{"role": "user", "content": [
                audio_part(wav),
                text_part("Transcribe this speech exactly. Output only the words spoken, nothing else."),
            ]}], max_tokens=200, temperature=0.0, kind="ears")
        except GemmaError as e:
            if time.time() - self._said_failure_at >= SAY_EVERY_S:
                self._said_failure_at = time.time()
                self.core.say(NO_AUDIO if e.code and "audio" in str(e).lower() else DEAF)
            raise
        text = strip_control(msg.get("content") or "").strip('"')
        return text, time.time() - start, str(path)
