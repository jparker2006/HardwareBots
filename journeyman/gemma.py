"""Client for an OpenAI-compatible Gemma server (vLLM, llama.cpp server, Ollama /v1).

Servers differ in how tool calls come back: parsed into `tool_calls`, or left in `content` as JSON,
a fenced block, or Gemma 4's native `<|tool_call>call:name{key:<|"|>value<|"|>}<tool_call|>` text.
`tool_calls()` reads all of them.

Live hardening: each call has a kind (agent, observe, look, ears, question, why) with its own timeout;
thinking is switched off (reasoning_effort "none" is the only switch Ollama /v1 honours) and any thinking
text is dropped before anyone reads the reply; response_format, chat_template_kwargs and reasoning_effort are dropped for good the first time the server rejects them; oversized and
surplus images are left out; max_tokens is capped per kind (JM_MAX_TOKENS_<KIND>). Failures raise GemmaError, whose message is plain enough for the screen."""
from __future__ import annotations

import base64
import json
import re
import socket
import threading
import time
import urllib.error

from .local_http import NotLocal, request_json

THINKING = [re.compile(r"<\|channel>.*?(?:<channel\|>|$)", re.S), re.compile(r"<think>.*?(?:</think>|$)", re.S),
            re.compile(r"<thought>.*?(?:</thought>|$)", re.S)]
REASONING_KEYS = ("reasoning_content", "reasoning", "thinking")
OPTIONAL = {"response_format": ("response_format", "json_object", "json_schema", "json mode", "guided"),
            "chat_template_kwargs": ("chat_template_kwargs", "enable_thinking"),
            "reasoning_effort": ("reasoning_effort",)}


class GemmaError(OSError):
    def __init__(self, text: str, code: int | None = None, kind: str = ""):
        super().__init__(text)
        self.code, self.kind = code, kind

    def __str__(self) -> str:
        return self.args[0]


class Gemma:
    def __init__(self, base_url: str, model: str = "", config=None):
        self.base = base_url.rstrip("/")
        self._model = model
        self.timeouts = dict(getattr(config, "timeouts", None) or {})
        self.thinking = bool(getattr(config, "thinking", False))
        self.json_mode = getattr(config, "json_mode", "auto")
        self.max_images = int(getattr(config, "max_images", 6))
        self.max_image_bytes = int(getattr(config, "max_image_kb", 512)) * 1024
        self.max_tokens = dict(getattr(config, "max_tokens", None) or {})
        self.unsupported: set[str] = set()
        self._lock = threading.Lock()

    def timeout(self, kind: str) -> float:
        return float(self.timeouts.get(kind) or self.timeouts.get("default") or 60.0)

    @property
    def model(self) -> str:
        if not self._model:
            with self._lock:
                if not self._model:
                    self._model = self._post(self.base + "/models", None, 10, "models")["data"][0]["id"]
        return self._model

    def chat(self, messages: list[dict], tools: list[dict] | None = None, json_mode: bool = False,
             max_tokens: int = 400, temperature: float = 0.2, kind: str = "default") -> tuple[dict, float]:
        cap = self.max_tokens.get(kind) or self.max_tokens.get("default")
        body: dict = {"model": self.model, "messages": limit_images(messages, self.max_images, self.max_image_bytes),
                      "max_tokens": min(max_tokens, cap) if cap else max_tokens, "temperature": temperature}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if json_mode and self.json_mode != "off":
            body["response_format"] = {"type": "json_object"}
        if not self.thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
            body["reasoning_effort"] = "none"
        start = time.time()
        while True:
            for key in self.unsupported:
                body.pop(key, None)
            try:
                out = self._post(self.base + "/chat/completions", body, self.timeout(kind), kind)
                break
            except GemmaError as e:
                key = self._rejected(e, body)
                if not key:
                    raise
                self.unsupported.add(key)
        try:
            msg = dict(out["choices"][0]["message"] or {})
        except (KeyError, IndexError, TypeError):
            raise GemmaError(f"Gemma sent a reply with no message ({kind})", kind=kind)
        return without_thinking(msg), time.time() - start

    def ask_json(self, messages: list[dict], max_tokens: int = 500, kind: str = "default") -> tuple[dict, float]:
        msg, secs = self.chat(messages, json_mode=True, max_tokens=max_tokens, kind=kind)
        return extract_json(flat_text(msg.get("content"))), secs

    def _rejected(self, e: GemmaError, body: dict) -> str | None:
        if e.code not in (400, 404, 415, 422, 500, 501):
            return None
        low = str(e).lower()
        for key, words in OPTIONAL.items():
            if key in body and any(w in low for w in words):
                return key
        if e.code in (400, 422) and "response_format" in body and self.json_mode == "auto" \
                and not re.search(r"image|audio|token|context|length|tool", low):
            return "response_format"
        return None

    def _post(self, url: str, body: dict | None, timeout: float, kind: str) -> dict:
        what = f"Gemma ({kind})" if kind != "default" else "Gemma"
        try:
            return request_json(url, body, timeout=timeout)
        except NotLocal:
            raise
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read() or b"{}")
                detail = detail.get("error", detail)
                detail = detail.get("message", detail) if isinstance(detail, dict) else detail
            except (ValueError, OSError, AttributeError):
                detail = e.reason
            raise GemmaError(f"{what} refused the request ({e.code}): {str(detail)[:160]}", e.code, kind)
        except (socket.timeout, TimeoutError):
            raise GemmaError(f"{what} took longer than {timeout:.0f}s, gave up", kind=kind)
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise GemmaError(f"{what} took longer than {timeout:.0f}s, gave up", kind=kind)
            raise GemmaError(f"can't reach Gemma at {self.base}; is the server running? ({e.reason})", kind=kind)
        except ConnectionError as e:
            raise GemmaError(f"lost the connection to Gemma at {self.base} ({e})", kind=kind)
        except ValueError:
            raise GemmaError(f"{what} sent back something that isn't JSON", kind=kind)


def without_thinking(msg: dict) -> dict:
    """The reply with reasoning fields and any thinking text taken out of content."""
    for key in REASONING_KEYS:
        msg.pop(key, None)
    content = msg.get("content")
    if isinstance(content, list):
        content = flat_text(content)
    if isinstance(content, str):
        for pattern in THINKING:
            content = pattern.sub(" ", content)
        msg["content"] = content.rpartition("</think>")[2].strip()
    return msg


def limit_images(messages: list[dict], most: int, max_bytes: int) -> list[dict]:
    """Keeps the newest `most` images under `max_bytes` each; the rest become a short text note."""
    seen = 0
    out = []
    for m in reversed(messages):
        content = m.get("content")
        if not isinstance(content, list):
            out.append(m)
            continue
        parts = []
        for p in reversed(content):
            if isinstance(p, dict) and p.get("type") == "image_url":
                url = (p.get("image_url") or {}).get("url", "")
                if len(url) * 3 // 4 > max_bytes:
                    p = text_part("(frame left out: too large)")
                elif seen >= most:
                    p = text_part("(frame left out)")
                else:
                    seen += 1
            parts.append(p)
        out.append(dict(m, content=parts[::-1]))
    return out[::-1]


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
