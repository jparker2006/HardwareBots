"""Settings, all from JM_* environment variables. Timeouts are per call kind: JM_TIMEOUT_<KIND> seconds."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


TIMEOUTS = {"default": 60, "agent": 45, "observe": 60, "look": 25, "ears": 20, "question": 15, "why": 20}


def _timeouts() -> dict:
    return {k: float(_env("JM_TIMEOUT_" + k.upper(), str(v))) for k, v in TIMEOUTS.items()}


@dataclass
class Config:
    gemma_url: str = field(default_factory=lambda: _env("JM_GEMMA_URL", "http://localhost:8000/v1"))
    gemma_model: str = field(default_factory=lambda: _env("JM_GEMMA_MODEL", ""))
    vision_url: str = field(default_factory=lambda: _env("JM_VISION_URL", ""))
    vision_model: str = field(default_factory=lambda: _env("JM_VISION_MODEL", ""))
    data_dir: Path = field(default_factory=lambda: Path(_env("JM_DATA", "data")))
    host: str = field(default_factory=lambda: _env("JM_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_env("JM_PORT", "3000")))
    camera: str = field(default_factory=lambda: _env("JM_CAMERA", "0"))
    frame_width: int = field(default_factory=lambda: int(_env("JM_FRAME_WIDTH", "512")))
    frame_interval_s: float = field(default_factory=lambda: float(_env("JM_FRAME_INTERVAL", "1.0")))
    observe_every_s: float = field(default_factory=lambda: float(_env("JM_OBSERVE_EVERY", "10")))
    question_gap_s: float = field(default_factory=lambda: float(_env("JM_QUESTION_GAP", "20")))
    silence_gate_s: float = field(default_factory=lambda: float(_env("JM_SILENCE_GATE", "1.5")))
    gap_confidence: float = field(default_factory=lambda: float(_env("JM_GAP_CONFIDENCE", "0.6")))
    max_tool_calls: int = field(default_factory=lambda: int(_env("JM_MAX_TOOL_CALLS", "4")))
    default_procedure: str = field(default_factory=lambda: _env("JM_PROCEDURE", "cable-swap"))
    expert_name: str = field(default_factory=lambda: _env("JM_EXPERT", "the expert"))
    timeouts: dict = field(default_factory=_timeouts)
    thinking: bool = field(default_factory=lambda: _env("JM_THINKING", "off").lower() in ("on", "1", "true", "yes"))
    json_mode: str = field(default_factory=lambda: _env("JM_JSON_MODE", "auto"))
    max_images: int = field(default_factory=lambda: int(_env("JM_MAX_IMAGES", "6")))
    max_image_kb: int = field(default_factory=lambda: int(_env("JM_MAX_IMAGE_KB", "512")))

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        if not self.vision_url:
            self.vision_url = self.gemma_url
        if not self.vision_model:
            self.vision_model = self.gemma_model

    @property
    def journal_path(self) -> Path:
        return self.data_dir / "events.jsonl"

    @property
    def frames_dir(self) -> Path:
        return self.data_dir / "frames"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def cards_dir(self) -> Path:
        return self.data_dir / "procedures"
