"""Speech to text with Gemma's audio input."""
from __future__ import annotations

import re
import time
import uuid

from .gemma import GemmaError, audio_part, text_part, strip_control

DEAF = "Sorry, I couldn't hear that. My speech model isn't answering."
NO_AUDIO = "Sorry, this model can't take audio. Type it on the monitor, or switch to a model that hears."
SAY_EVERY_S = 30.0
REFUSAL = re.compile(r"\bi need an? (audio|video|sound)\b|\b(provide|upload|share|attach) (an? |the )?(audio|sound|video)\b"
                     r"|\bno audio (was |is )?(provided|attached|included)\b", re.I)


def no_audio(text: str) -> bool:
    """True when the model answered without the audio, e.g. Ollama /v1 drops input_audio and Gemma asks for a file."""
    return bool(REFUSAL.search(text or ""))


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
            text = strip_control(msg.get("content") or "").strip('"')
            if no_audio(text):
                raise GemmaError(f"Gemma (ears) answered without the audio: {text[:80]!r}", 415, "ears")
        except GemmaError as e:
            if time.time() - self._said_failure_at >= SAY_EVERY_S:
                self._said_failure_at = time.time()
                self.core.say(NO_AUDIO if e.code and "audio" in str(e).lower() else DEAF)
            raise
        return text, time.time() - start, str(path)
