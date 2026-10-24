"""Client for an OpenAI-compatible Gemma server (vLLM, llama.cpp server, Ollama /v1)."""
from __future__ import annotations

import base64
import json
import re
import time

from .local_http import request_json


class Gemma:
    def __init__(self, base_url: str, model: str = ""):
        self.base = base_url.rstrip("/")
        self._model = model

    @property
    def model(self) -> str:
        if not self._model:
            self._model = request_json(self.base + "/models", timeout=10)["data"][0]["id"]
        return self._model

    def chat(self, messages: list[dict], tools: list[dict] | None = None, json_mode: bool = False,
             max_tokens: int = 400, temperature: float = 0.2) -> tuple[dict, float]:
        body: dict = {"model": self.model, "messages": messages, "max_tokens": max_tokens,
                      "temperature": temperature}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        start = time.time()
        out = request_json(self.base + "/chat/completions", body)
        return out["choices"][0]["message"], time.time() - start

    def ask_json(self, messages: list[dict], max_tokens: int = 500) -> tuple[dict, float]:
        msg, secs = self.chat(messages, json_mode=True, max_tokens=max_tokens)
        return extract_json(msg.get("content") or ""), secs


def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def image_part(jpeg: bytes) -> dict:
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}


def audio_part(wav: bytes) -> dict:
    return {"type": "input_audio", "input_audio": {"data": base64.b64encode(wav).decode(), "format": "wav"}}


def extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {"value": value}
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {}
