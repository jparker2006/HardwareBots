# Journeyman

**A fully local AI instructor that learns a senior tech's hands-on work by asking "why?", then teaches the next person by voice.** It runs on Gemma 4 on an NVIDIA Jetson AGX Thor. No cloud.

It watches and listens while a senior technician works and asks about the steps they don't explain ("You labeled both ends before plugging in. Why?"). It saves what it learns as a procedure card, and each item records whether the expert said it or the AI guessed it. Then it teaches the next person by voice: it checks their work with the camera, catches skipped steps, answers "why?" in the expert's own words, and says "not captured" instead of guessing. Every trainee slip and every unanswered question goes back into its memory.

Journeyman turns a senior tech's hands-on work into skills: for people today, robots tomorrow.

- **Built at** the Google Gemma Hardware Hackathon (AGI House, 2026-10-24), with Gemma 4.
- **Track:** Physical AI, Robotics and Embedded Devices (Embedded track). Also entering Best Use of LiteRT ([litert/](litert/README.md)).
- **Status:**
  - *Working:* the full capture → teach → improve loop runs end to end in our tests against a scripted Gemma stand-in.
  - *Working, but not on Thor:* real Gemma 4 E2B on a CPU-only Linux box handled every call type ([docs/CONFORMANCE.md](docs/CONFORMANCE.md)). LiteRT-LM ran Gemma 4 E2B there and answered card questions offline ([litert/README.md](litert/README.md)).
  - *Target, not yet verified:* Gemma 4 31B on Thor, live Brio camera checks, voice over USB-C, and speech-to-text on the Mac through LiteRT-LM.
- **Docs:** [pitch](docs/PITCH.md), [demo run sheet](docs/DEMO.md), [submission](docs/SUBMISSION.md), [architecture](docs/ARCHITECTURE.md).

```
 Mac (headset): mic + speaker, LiteRT-LM Gemma 4 E2B ──USB-C──▶ Thor: journeyman service + Gemma 4 31B (vLLM) + Brio camera ──HDMI──▶ monitor
```
On Thor, `scripts/thor-up.sh` starts the models, checks them, and runs `jm serve` ([scripts/README.md](scripts/README.md)).

## Run it
One person can run the whole demo. The minute-by-minute run sheet and a fallback for each failure: [docs/DEMO.md](docs/DEMO.md).

### 1. Thor: Gemma + Journeyman, one command
```sh
git clone https://github.com/jparker2006/Journeyman.git && cd Journeyman
scripts/thor-up.sh --only-31b     # vLLM Gemma 4 31B NVFP4 (text + image), jm doctor, then jm serve with the camera
source /tmp/journeyman/env.sh     # the settings thor-up chose, for later ./jm commands
```
`scripts/thor-up.sh` with no option also starts Gemma 4 E4B (audio) if it is on Thor. Without E4B there is no audio input, so use the headset with `--text`. `scripts/thor-up.sh --dry-run` prints the commands; `scripts/thor-down.sh` stops everything. Details: [scripts/README.md](scripts/README.md). Open `http://localhost:3000` on the monitor; `./jm doctor` checks Gemma, the camera and whether you are offline.

### 2. Mac: the headset
```sh
git clone https://github.com/jparker2006/Journeyman.git && cd Journeyman
python3 headset/headset.py --text    # type; replies are spoken on the Mac. Needs nothing installed
pip3 install sounddevice numpy && python3 headset/headset.py    # talk (needs E4B on Thor)
```
It finds Thor at `192.168.55.1` (USB-C link) or `192.168.3.131` (venue Wi-Fi). Pin one with `--thor http://<address>:3000`.

### 3. Seed and reset
```sh
./jm seed recordings/demo            # load the rehearsed cable-swap card (no model needed); refuses if the card has steps
./jm reset                           # archive data/ to data/archive/<time>/ and start clean; stop jm serve first
./jm reset --seed recordings/demo    # both
```
Stop and restart the service around a reset: `kill $(cat /tmp/journeyman/serve.pid)`, reset, then `scripts/thor-up.sh --only-31b --no-doctor` (it reuses the running model).

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
| `JM_PROFILE` | (none) | `thor31b`: short calls for the dense 31B on Thor (3 frames at 384 px under 96 KB, 20 s observe interval, replies capped at 220/60/160 tokens for observe/look/agent, compact JSON, timeouts 75/30/60 s). Any `JM_*` set explicitly wins; see `PROFILES` in `journeyman/config.py` |
| `JM_MAX_TOKENS_<KIND>` | (no cap) | Reply cap per call kind: `observe`, `look`, `agent`, `why`, `question`, `ears` |

## Tests
```sh
python3 -m unittest discover -s tests -t .
```
They run the full capture → teach → improve loop against a scripted stand-in for Gemma.

## License
[Apache 2.0](LICENSE)
