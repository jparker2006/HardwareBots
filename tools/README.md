# Bring-up checklist: `jm doctor`

Run this first on Thor (or the Mac). One line per check: PASS / WARN / FAIL plus what to do next. Exit code 0 only if nothing FAILed.
```sh
./jm doctor                 # Python, Gemma server (JM_GEMMA_URL, else probes :8000 vLLM, :8080 llama.cpp, :11434 Ollama),
                            # text / image / audio / tool calling, camera, journeyman service, internet, disk
./jm doctor --offline       # demo mode: internet reachable is a FAIL
./jm doctor --json          # machine output
./jm bench --runs 5         # median and min per probe; built-in JPEG and WAV unless --image / --wav given
```

# Gemma timing probe

Measures how fast and how accurately the local Gemma server answers one image question and one short audio clip. It's a measurement tool, not project code. Python 3 standard library only.

## 1. Prep (on the Mac, 2 min)
```sh
# Photo of someone holding a phone (take a second one without a phone as a control)
sips -Z 1024 phone.jpg

# 3-second Voice Memo of someone reading a line, converted to 16 kHz WAV
afconvert -f WAVE -d LEI16@16000 memo.m4a line.wav
```

## 2. Run
```sh
git clone https://github.com/jparker2006/HardwareBots.git && cd HardwareBots
python3 tools/gemma_probe.py http://<thor-ip>:<port>/v1 phone.jpg line.wav
```
- The model name is detected automatically. Override it with `--model NAME`.
- Usual ports: vLLM `8000`, llama.cpp server `8080`, Ollama `11434`.
- Over the Jetson USB link, Thor is often `192.168.55.1` (assumed; check yours).
- Running on Thor itself, use `http://localhost:<port>/v1`.

## 3. Optional power check (on Thor, read-only)
```sh
sudo tegrastats --interval 500   # note any watt values: once idle, once while the probe runs
```

## 4. Send back
The six timing lines, the model/runtime name, the tegrastats watt values, and whether the camera is plugged into Thor or the Mac.

| Result | Meaning |
|---|---|
| Image ≲ 2 s and correct | Vision-heavy ideas are realistic |
| Audio ≤ 1.5 s, words right | Live speech-triggered ideas are realistic |
| Audio > 3 s or wrong | Speech can't drive real-time beats |
| Audio errors out | This runtime may not take audio input; try vLLM |
| Watts swing ≥ 15 W within seconds | Power-control ideas stay alive |
