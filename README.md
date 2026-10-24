# Journeyman

**A fully local AI instructor that learns a senior tech's hands-on work by asking "why?", then teaches the next person by voice.** It runs on Gemma 4 on an NVIDIA Jetson AGX Thor. No cloud.

It watches and listens while a senior technician works and asks about the steps they don't explain ("You labeled both ends before plugging in. Why?"). It saves what it learns as a procedure card, and each item records whether the expert said it or the AI guessed it. Then it teaches the next person by voice: it checks their work with the camera, catches skipped steps, answers "why?" in the expert's own words, and says "not captured" instead of guessing. Every trainee slip and every unanswered question goes back into its memory.

Journeyman turns a senior tech's hands-on work into skills: for people today, robots tomorrow.

- **Built at** the Google Gemma Hardware Hackathon (AGI House, 2026-10-24), with Gemma 4.
- **Track:** Physical AI, Robotics and Embedded Devices (Embedded track).
- **Status:** the full capture → teach → improve loop passes end-to-end tests against a scripted Gemma stand-in. Running live on Thor with the Brio camera and voice is the target, and isn't verified yet.
- **Docs:** [pitch](docs/PITCH.md), [demo run sheet](docs/DEMO.md), [submission](docs/SUBMISSION.md), [architecture](docs/ARCHITECTURE.md).

```
 Mac (headset): mic + speaker  ──USB-C──▶  Thor: journeyman service + Gemma 4 + Brio camera ──HDMI──▶ monitor
```

## Run it

### 1. Thor: start Gemma
Gemma has to serve an OpenAI-compatible API. Audio input needs vLLM and the E2B or E4B model. Jetson AI Lab's command:
```sh
sudo docker run -it --rm --pull always --runtime=nvidia --network host \
  -v $HOME/.cache/huggingface:/root/.cache/huggingface \
  ghcr.io/nvidia-ai-iot/vllm:gemma4-jetson-thor \
  vllm serve google/gemma-4-E4B-it --enable-auto-tool-choice --reasoning-parser gemma4 --tool-call-parser gemma4
```
Check it with `python3 tools/gemma_probe.py http://localhost:8000/v1 photo.jpg clip.wav` (see [tools/README.md](tools/README.md)).

### 2. Thor: start Journeyman
```sh
git clone https://github.com/jparker2006/Journeyman.git && cd journeyman
python3 -c "import cv2" || pip3 install opencv-python   # camera; GStreamer is used if OpenCV is missing
JM_GEMMA_URL=http://localhost:8000/v1 ./jm serve
```
Open `http://localhost:3000` on the monitor. `./jm doctor` checks Gemma, the camera, and whether you are offline.

### 3. Mac: start the headset
```sh
git clone https://github.com/jparker2006/Journeyman.git && cd journeyman
pip3 install sounddevice numpy
python3 headset/headset.py --thor http://192.168.55.1:3000          # talk
python3 headset/headset.py --thor http://192.168.55.1:3000 --text   # or type
```
`192.168.55.1` is the usual address of a Jetson over the USB-C link. Use whatever address reaches your Thor.

## Talk to it
| Say | What happens |
|---|---|
| "Journeyman, start capture" | Records the expert. It asks "why?" about steps you don't explain |
| "I'm done" | Ends the session and reads back what it saved |
| "Teach me" | New-hire mode: it gives the steps by voice, one at a time |
| "Next" / "Done" | Checks the step with the camera, then moves on or tells you what you skipped |
| Any question ("why do I leave slack?") | Answers from what the expert said, or says it isn't captured and queues the question |

## The CLI is the agent's toolbox
Every Gemma tool is a `jm` command. You, a script, or Gemma can call the same operations.
```sh
./jm status
./jm ls /procedures
./jm card cable-swap
./jm search-knowledge "slack"
./jm look "Is the cable labeled?"
./jm confirm /procedures/cable-swap/2@r1     # expert-only; refused if the step changed since you looked
./jm schema                                   # the tool schemas Gemma sees
./jm replay recordings/demo --speed 2         # feed a recorded session through the real pipeline
./jm bench --image photo.jpg --wav clip.wav   # latency of each Gemma call
```

## How it works
- **One service owns state.** The voice headset, the CLI, the monitor page and Gemma's tools all use one operation set ([journeyman/ops.py](journeyman/ops.py)).
- **Append-only event log.** Everything heard, seen, asked, answered and saved is an event in `data/events.jsonl`. The knowledge base is rebuilt from it, and procedure cards are exported to `data/procedures/*.md`.
- **Memory is data, not instructions.** Every saved reason records whether the expert said it or the AI inferred it, and whether it is confirmed.
- **Local only, enforced in one place.** [journeyman/local_http.py](journeyman/local_http.py) refuses any host that isn't this machine or the local link. The service only answers local clients.
- **Bounded.** At most 4 tool calls per turn, and one question every 20 s. Speech output never overlaps the mic.

Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Settings
| Variable | Default | Meaning |
|---|---|---|
| `JM_GEMMA_URL` | `http://localhost:8000/v1` | Gemma server (ears, agent) |
| `JM_VISION_URL` | same as `JM_GEMMA_URL` | Optional separate vision model, e.g. 26B-A4B |
| `JM_CAMERA` | `0` | Camera index or device; `none` to only accept pushed frames |
| `JM_PORT` | `3000` | Journeyman service port |
| `JM_DATA` | `data` | Event log, frames, audio, procedure cards |
| `JM_PROCEDURE` | `cable-swap` | Default procedure name |
| `JM_EXPERT` | `the expert` | How it refers to the expert |
| `JM_OBSERVE_EVERY` | `10` | Seconds between "what did they do?" checks |
| `JM_QUESTION_GAP` | `20` | Minimum seconds between questions |

## Tests
```sh
python3 -m unittest discover -s tests -t .
```
They run the full capture → teach → improve loop against a scripted stand-in for Gemma.

## License
[Apache 2.0](LICENSE)
