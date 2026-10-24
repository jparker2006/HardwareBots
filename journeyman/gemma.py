"""Client for an OpenAI-compatible Gemma server (vLLM, llama.cpp server, Ollama /v1).

Servers differ in how tool calls come back: parsed into `tool_calls`, or left in `content` as JSON,
a fenced block, or Gemma 4's native `<|tool_call>call:name{key:<|"|>value<|"|>}<tool_call|>` text.
`tool_calls()` reads all of them."""
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


NATIVE_CALL = re.compile(r"<\|tool_call>\s*call:([\w.-]+)\s*(\{.*?\})\s*(?:<tool_call\|>|<turn\|>|$)", re.S)
BARE_CALL = re.compile(r"(?:<call>|(?:^|\s)call:)([\w.-]+)\s*(\{.*?\})", re.S)
NATIVE_ARG = re.compile(r'([\w.-]+)\s*:\s*(?:<\|"\|>(.*?)<\|"\|>|"((?:[^"\\]|\\.)*)"|([^,}]*))', re.S)
CONTROL = [re.compile(r"<\|channel>.*?(?:<channel\|>|$)", re.S), re.compile(r"<\|tool_call>.*?(?:<tool_call\|>|$)", re.S),
           re.compile(r'<\|"\|>|<\|[\w"]+>|<[\w"]+\|>')]


def flat_text(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(p.get("text", "") for p in content or [] if isinstance(p, dict) and p.get("type") == "text")


def strip_control(text: str) -> str:
    for pattern in CONTROL:
        text = pattern.sub(" ", text)
    return text.strip()


def tool_calls(msg: dict) -> list[dict]:
    """Every tool call in a reply, as {"id", "name", "arguments": dict}, wherever the server put it."""
    calls = _as_calls(msg.get("tool_calls") or [])
    if not calls:
        text = flat_text(msg.get("content"))
        calls = _native_calls(text) or (_json_calls(text) if "{" in text else [])
    for i, call in enumerate(calls):
        if not call.get("id"):
            call["id"] = f"call_{i}"
    return calls


def _native_calls(text: str) -> list[dict]:
    found = NATIVE_CALL.findall(text) or BARE_CALL.findall(text)
    return [{"name": name, "arguments": _native_args(body)} for name, body in found]


def _native_args(body: str) -> dict:
    try:
        value = json.loads(body)
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    args = {}
    for key, native, quoted, raw in NATIVE_ARG.findall(body[1:-1]):
        if native or quoted or not raw.strip():
            args[key] = native or quoted
            continue
        try:
            args[key] = json.loads(raw.strip())
        except ValueError:
            args[key] = raw.strip()
    return args


def _json_calls(text: str) -> list[dict]:
    calls = []
    for chunk in re.findall(r"```[\w]*\s*(.*?)```", text, re.S) or [text]:
        try:
            value = json.loads(chunk.strip())
        except ValueError:
            value = extract_json(chunk)
        calls += _as_calls(value)
    return calls


def _as_calls(item) -> list[dict]:
    if isinstance(item, list):
        return [c for x in item for c in _as_calls(x)]
    if not isinstance(item, dict):
        return []
    if isinstance(item.get("tool_calls"), list):
        return _as_calls(item["tool_calls"])
    for key in ("function", "tool_call", "function_call"):
        if isinstance(item.get(key), dict):
            return [dict(c, id=item.get("id") or c.get("id")) for c in _as_calls(item[key])]
    name = item.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[\w.-]+", name):
        return []
    return [{"name": name, "arguments": _args(item.get("arguments", item.get("parameters", item.get("args"))))}]


def _args(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value or "{}")
        except ValueError:
            value = extract_json(value)
    return value if isinstance(value, dict) else {}


def merge_roles(messages: list[dict]) -> list[dict]:
    """For chat templates without system or tool roles: fold the system prompt into the first user turn
    and send tool calls and results as plain text."""
    out: list[dict] = []
    system = ""
    for m in messages:
        role, content = m["role"], m.get("content") or ""
        if role == "system":
            system += flat_text(content) + "\n\n"
            continue
        if role == "tool":
            role, content = "user", f"Result of {m.get('name') or 'the tool'}: {flat_text(content)}"
        elif role == "assistant" and m.get("tool_calls"):
            content = "\n".join(json.dumps({"name": c["function"]["name"], "arguments": _args(c["function"].get("arguments"))})
                                for c in m["tool_calls"])
        if role == "user" and system:
            content = system + content if isinstance(content, str) else [text_part(system)] + list(content)
            system = ""
        if out and out[-1]["role"] == role and isinstance(content, str) and isinstance(out[-1]["content"], str):
            out[-1] = {"role": role, "content": out[-1]["content"] + "\n\n" + content}
        else:
            out.append({"role": role, "content": content})
    return out
