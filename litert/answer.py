"""Answer a new hire's question from a procedure card with Gemma 4 E2B on LiteRT-LM.

Runs fully offline on the Mac (or any Linux box): the card is the exported Markdown
from Thor (data/procedures/<name>.md), the model is a local .litertlm file.

    python3 litert/answer.py --card litert/sample_card.md "why do I leave slack?"
    python3 litert/answer.py --card card.md --bench "q1" "q2"     # one load, timings per question

From code (e.g. the headset):

    from litert.answer import ask
    ask("why do I leave slack?", card_path="data/procedures/cable-swap.md")
    # {"answer": "...", "captured": True, "seconds": 0.8}

This file imports on Python 3.9; the litert_lm package itself needs Python 3.10+.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

NOT_CAPTURED = "That isn't captured on the card yet."
UNCONFIRMED = "The expert hasn't confirmed that yet."
MARKER = "NOT CAPTURED"
DEFAULT_MODEL = os.environ.get("JM_LITERT_MODEL", str(Path.home() / "models" / "gemma-4-E2B-it.litertlm"))
CACHE_DIR = os.environ.get("JM_LITERT_CACHE", str(Path.home() / ".cache" / "journeyman-litert"))

SYSTEM = (
    "You are Journeyman, a shop-floor instructor talking to a new hire by voice. "
    "Answer ONLY from the procedure card the user gives you. "
    "Reply in one or two short spoken sentences, no Markdown, no lists. "
    "If the card does not contain the answer, reply with exactly: " + MARKER + ". "
    "Never guess, never use outside knowledge."
)


def build_prompt(card: str, question: str) -> str:
    return f"PROCEDURE CARD:\n{card.strip()}\n\nQUESTION: {question.strip()}"


def clean(raw: str) -> tuple[str, bool]:
    text = re.sub(r"[*_`#]+", "", raw or "").strip()
    if not text or MARKER.lower() in text.lower() or "not captured" in text.lower():
        return NOT_CAPTURED, False
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    return " ".join(sentences[:2]), True


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 3}


def caveat(card: str, answer: str) -> str:
    said = _words(answer)
    for line in card.splitlines():
        if "(unconfirmed)" in line:
            words = _words(re.sub(r"\*\*\w+:\*\*|\(unconfirmed\)", "", line))
            if words and len(words & said) >= max(2, len(words) // 2):
                return f"{answer} {UNCONFIRMED}"
    return answer


def _message_text(msg) -> str:
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict):
        return "".join(c.get("text", "") for c in msg.get("content", []) if isinstance(c, dict))
    return "".join(getattr(c, "text", "") or "" for c in getattr(msg, "contents", []) or [])


class Answerer:
    """Holds one loaded LiteRT-LM engine; each question gets a fresh conversation."""

    def __init__(self, model_path: str = DEFAULT_MODEL, backend: str = "cpu", threads: int | None = None,
                 max_tokens: int = 4096, engine=None):
        self.engine = engine or self._load(model_path, backend, threads, max_tokens)

    @staticmethod
    def _load(model_path, backend, threads, max_tokens):
        try:
            import litert_lm
        except ImportError:
            raise RuntimeError("litert_lm is not installed (needs Python 3.10+): see litert/README.md")
        if not Path(model_path).is_file():
            raise RuntimeError(f"model not found: {model_path} (set JM_LITERT_MODEL or --model)")
        os.makedirs(CACHE_DIR, exist_ok=True)
        b = litert_lm.Backend.GPU() if backend == "gpu" else litert_lm.Backend.CPU(thread_count=threads)
        return litert_lm.Engine(model_path, backend=b, max_num_tokens=max_tokens, cache_dir=CACHE_DIR)

    def _conversation(self):
        try:
            import litert_lm
            sampler = litert_lm.SamplerConfig(temperature=0.0, top_k=1)
        except ImportError:
            sampler = None
        return self.engine.create_conversation(system_message=SYSTEM, sampler_config=sampler, max_output_tokens=96)

    def answer(self, card: str, question: str) -> dict:
        t = time.time()
        if not card.strip() or not question.strip():
            return {"answer": NOT_CAPTURED, "captured": False, "seconds": 0.0}
        conv = self._conversation()
        try:
            raw = _message_text(conv.send_message(build_prompt(card, question)))
        finally:
            close = getattr(conv, "close", None)
            if close:
                close()
        text, captured = clean(raw)
        if captured:
            text = caveat(card, text)
        return {"answer": text, "captured": captured, "seconds": round(time.time() - t, 2)}


_shared: Answerer | None = None


def ask(question: str, card_path: str | None = None, card_text: str | None = None, **engine_args) -> dict:
    """One call for the headset: loads the model once per process, then answers from the card."""
    global _shared
    if _shared is None:
        _shared = Answerer(**engine_args)
    card = card_text if card_text is not None else Path(card_path).read_text()
    return _shared.answer(card, question)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Answer a question from a procedure card with Gemma on LiteRT-LM.")
    p.add_argument("question", nargs="+", help="one or more questions")
    p.add_argument("--card", required=True, help="procedure card Markdown, e.g. data/procedures/cable-swap.md")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f".litertlm file (default {DEFAULT_MODEL})")
    p.add_argument("--backend", choices=["cpu", "gpu"], default="cpu")
    p.add_argument("--threads", type=int, default=None)
    p.add_argument("--bench", action="store_true", help="print load and per-question latency")
    a = p.parse_args(argv)
    card = Path(a.card).read_text()
    t = time.time()
    try:
        ans = Answerer(a.model, a.backend, a.threads)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.bench:
        print(f"[load {time.time() - t:.2f}s, backend {a.backend}]")
    for q in a.question:
        r = ans.answer(card, q)
        prefix = f"[{r['seconds']:.2f}s{'' if r['captured'] else ', not captured'}] " if a.bench else ""
        print(f"{prefix}{r['answer']}" if len(a.question) == 1 else f"Q: {q}\n{prefix}{r['answer']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
