#!/usr/bin/env python3
"""Feasibility probe: time one image question and one audio transcription
against any OpenAI-compatible Gemma server (vLLM, llama.cpp server, Ollama /v1).

Standard library only. Run from the Mac or from Thor.

  python3 tools/gemma_probe.py http://192.168.55.1:8000/v1 phone.jpg line.wav
  python3 tools/gemma_probe.py http://localhost:8080/v1 phone.jpg --model gemma-4-E4B-it
"""
import argparse, base64, json, time, urllib.request


def request(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.load(resp)


def ask(base, model, content):
    start = time.time()
    out = request(base + "/chat/completions", {
        "model": model, "max_tokens": 64, "temperature": 0,
        "messages": [{"role": "user", "content": content}],
    })
    return time.time() - start, out["choices"][0]["message"]["content"]


def b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def run(label, base, model, content, runs):
    for i in range(runs):
        try:
            secs, text = ask(base, model, content)
            print(f"{label} run {i + 1}: {secs:.2f}s  {text!r}")
        except Exception as e:  # report and keep going; an error is a finding too
            print(f"{label} run {i + 1}: ERROR {e}")


p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument("base_url", help="server base URL ending in /v1")
p.add_argument("image", help="JPEG photo (shrink first: sips -Z 1024 photo.jpg)")
p.add_argument("audio", nargs="?", help="optional 16 kHz WAV clip, about 3 s")
p.add_argument("--model", help="model name; default: first model the server lists")
p.add_argument("--runs", type=int, default=3)
args = p.parse_args()

base = args.base_url.rstrip("/")
model = args.model or request(base + "/models")["data"][0]["id"]
print(f"server {base}  model {model}")

run("image", base, model, [
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64(args.image)}},
    {"type": "text", "text": 'Is a person holding a phone in this image? Reply only JSON: {"answer": true|false}'},
], args.runs)

if args.audio:
    run("audio", base, model, [
        {"type": "input_audio", "input_audio": {"data": b64(args.audio), "format": "wav"}},
        {"type": "text", "text": "Transcribe this audio exactly."},
    ], args.runs)
