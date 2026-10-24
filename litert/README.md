# LiteRT-LM on the Mac: offline answers for the new hire

Thor does the heavy work (camera, capture, vLLM). Once a procedure card exists, the new hire's
"why?" questions can be answered on the Mac by Gemma 4 E2B running through
[LiteRT-LM](https://github.com/google-ai-edge/LiteRT-LM). It reads only the card. If the answer isn't
on the card it says "That isn't captured on the card yet." No network once the model is downloaded.

`answer.py` gives you a CLI and an `ask()` function the headset can call later. Nothing in `headset/` uses it yet.

## Mac setup (Apple Silicon)
The `litert-lm-api` wheel needs Python 3.10+. macOS system Python is 3.9, so use a venv made with `uv`:
```sh
curl -LsSf https://astral.sh/uv/install.sh | sh          # or: brew install uv
uv venv ~/jm-litert --python 3.12
uv pip install -p ~/jm-litert litert-lm-api              # add litert-lm if you also want the litert-lm CLI
mkdir -p ~/models
curl -L -o ~/models/gemma-4-E2B-it.litertlm \
  https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm/resolve/main/gemma-4-E2B-it.litertlm   # 2.6 GB, not gated
```
Get the card from Thor. Either copy `data/procedures/<name>.md`, or fetch it from the service:
```sh
curl -s -X POST http://192.168.55.1:3000/api/ops/card -d '{}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"])' > card.md
```
Ask:
```sh
~/jm-litert/bin/python litert/answer.py --card card.md "why do I leave slack?"
~/jm-litert/bin/python litert/answer.py --card litert/sample_card.md --bench "why do I leave slack?" "what torque do the screws need?"
~/jm-litert/bin/python litert/answer.py --card card.md --backend gpu "..."   # Metal GPU, unverified
```
From Python: `from litert.answer import ask; ask("why?", card_path="card.md")` returns
`{"answer": str, "captured": bool, "seconds": float}`. The model loads once per process.
Settings: `JM_LITERT_MODEL` (model path, default `~/models/gemma-4-E2B-it.litertlm`) and `JM_LITERT_CACHE` (default `~/.cache/journeyman-litert`).

## How it answers
- Each question starts a new conversation: system rules, then the whole card, then the question. Decoding is greedy, with at most 96 output tokens.
- The model is told to reply `NOT CAPTURED` when the card doesn't have the answer. `clean()` turns that into the fixed "not captured" sentence and cuts any answer to two sentences.
- E2B ignored the prompt rule about unconfirmed reasons, so that check is in code: `caveat()` adds "The expert hasn't confirmed that yet." when the answer clearly comes from a line marked *(unconfirmed)*.

## What was verified (2026-10-24, sandbox: Linux aarch64, 4 CPU cores, no GPU)
- `litert-lm` 0.18.1 from PyPI installs and runs `gemma-4-E2B-it.litertlm` on CPU. `litert-lm describe` reports these inputs: Text, Vision and Audio. Text backends: CPU and GPU. Vision and audio backends: CPU only. Context: 32k.
- Measured with `litert/sample_card.md` (~350 tokens):
  - First load: 5.2 s. It writes a 788 MB XNNPACK cache.
  - Later loads: 0.25 s.
  - Each answer: 1.6–2.3 s. "Not captured" answers are the fastest, at ~1.6 s.
  - Peak memory: 1.4 GB.
- On the sample card, 8 of 8 questions were handled correctly: the 6 captured ones answered from the card, plus "torque" and "wifi password" both came back not captured.
- `tests/test_litert.py` checks the logic against a fake engine, so it needs neither the model nor litert_lm. It passes on Python 3.9 and 3.11.

## Unverified
- Anything on a real Mac: install, Metal GPU backend, speed. Google's model card reports E2B on an M4 Max at CPU 901 prefill / 41.6 decode tok/s and GPU 7,835 / 160.2 tok/s. The `-gpu.litertlm` file (2.0 GB) is the one made for GPU.
- Audio or image input through LiteRT-LM. The model says it supports them; this prototype only sends text.
- Cards exported by a live Thor session. The sample uses the same layout as `State.card_markdown`.
- Whether the Mac counts as a local client for `POST /api/ops/card` over the USB-C link.

## Sources
- https://github.com/google-ai-edge/LiteRT-LM (README, uv quick start, Python API)
- https://ai.google.dev/edge/litert-lm/python and https://ai.google.dev/edge/litert-lm/cli
- https://huggingface.co/litert-community/gemma-4-E2B-it-litert-lm (Apache-2.0, ungated, benchmark tables)
- https://pypi.org/project/litert-lm-api/ (wheels: macOS 12+ arm64, manylinux aarch64/x86_64, Windows; Python >= 3.10)
