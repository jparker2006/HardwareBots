"""Speech to text with Gemma's audio input."""
from __future__ import annotations

import time
import uuid

from .gemma import audio_part, text_part


class Ears:
    def __init__(self, core):
        self.core = core

    def transcribe(self, wav: bytes) -> tuple[str, float, str]:
        clip_id = uuid.uuid4().hex[:8]
        path = self.core.config.audio_dir / f"{clip_id}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(wav)
        start = time.time()
        msg, _ = self.core.gemma.chat([{"role": "user", "content": [
            audio_part(wav),
            text_part("Transcribe this speech exactly. Output only the words spoken, nothing else."),
        ]}], max_tokens=200, temperature=0.0)
        text = (msg.get("content") or "").strip().strip('"')
        return text, time.time() - start, str(path)
