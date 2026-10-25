# Submission draft (AGI House event site)

Save the draft by **7:00 PM PT** (finalists are picked from drafts). Submit by **8:00 PM PT**.
Each block below pastes straight into a form field. Before the 8 PM submit, go through [Before 8 PM](#before-8-pm) and change every *target* that we actually saw work to plain words. Leave the rest as *target*.

---

**Title:** Journeyman

**Tagline:** A fully local AI instructor that learns a senior tech's hands-on work by asking "why?", then teaches the next person by voice.

**Description:**
Every shop has a senior tech whose know-how was never written down. When they explain a job, they leave out most of their decisions. Journeyman watches and listens while the expert works, with a webcam on an NVIDIA Jetson AGX Thor and a Mac as the mic and speaker. It spots steps the expert did but didn't explain and asks about them on the spot: "You labeled both ends before plugging in. Why?" Each answer goes onto a procedure card, tagged with whether the expert said it or the AI guessed it. Then Journeyman teaches a new hire by voice, one step at a time. It checks each step with the camera, answers "why?" in the expert's own words, and says "not captured" instead of guessing. Any question it couldn't answer goes into a queue for the expert's next session. It runs on Gemma 4, entirely on-device, with no cloud.

**How it works:**
```
 MAC (headset)                          JETSON AGX THOR
 ┌──────────────────────┐              ┌───────────────────────────────────────────────┐
 │ mic → speech detect  │── USB-C ───▶ │ journeyman service (one core, one op set)     │
 │ macOS `say` ◀── text │◀── link ──── │   ears     speech → text                      │
 │ LiteRT-LM Gemma 4 E2B│              │   eyes     Brio keyframes → Gemma vision      │
 │  "why?" from the card│              │   agent    Gemma 4 + tools (= `jm` commands)  │
 └──────────────────────┘              │   journal  events.jsonl, append-only          │
                                       │   cards    procedures/*.md, rebuilt from log  │
 Logitech Brio ──── USB ─────────────▶ │   monitor  http :3000 ── HDMI ──▶ screen      │
                                       │ vLLM: Gemma 4 31B NVFP4 (text + image)        │
                                       └───────────────────────────────────────────────┘
 capture:  expert works → observe frames → unexplained step? → ask "why?" → save step + reason
 teach:    "teach me" → speak step → "next" → camera check → next step, or name the skipped one
 improve:  question not on the card → "not captured" → queued for the expert
```
- **Agent:** Gemma calls tools, and each tool is a `jm` CLI command: `save_step`, `save_why`, `search_knowledge`, `look`, `log_question`, `speak`. At most 4 tool calls per turn, and at most one question every 20 s.
- **Memory is data, not instructions.** An append-only event log is the source of truth. The knowledge base and Markdown cards are rebuilt from it. Each reason records who said it and whether it is confirmed. Changing a confirmed item needs the expert's spoken yes.
- **Local only, enforced in one place:** every network call goes through one client that refuses any host other than this machine and the USB-C link. `jm doctor --offline` checks it.
- **Headless replay:** `jm replay` runs a recorded session through the same pipeline, for tests and as the demo backup.

**What runs where:**
| Where | What |
|---|---|
| Thor | Gemma 4 31B NVFP4 on vLLM (eyes and agent); the Journeyman service (capture, teach, agent, event log, cards); Brio capture through GStreamer; the monitor page over HDMI |
| Mac | The headset: mic, speech detection, macOS `say`. LiteRT-LM with Gemma 4 E2B answering the new hire's "why?" from the card (`litert/answer.py`; not yet wired into the headset). Gemma speech-to-text on the Mac through LiteRT-LM is the *target* (the 31B on Thor can't take audio) |
| Fallback | Typed input (`headset.py --text`) goes through the same pipeline. `jm replay` and a seeded demo card cover a model or camera failure |

**What's local:** everything. The models, speech, vision, the agent, the knowledge base, the event log and the monitor page all run on Thor and the Mac. The Mac and Thor talk over a cable. There are no cloud calls anywhere in the loop. The demo is meant to run with Wi-Fi off (*target*).

**Tech stack:** Gemma 4 31B NVFP4 (vision and agent) on vLLM (Jetson Thor container, OpenAI-compatible, Gemma 4 tool-call parser). Gemma 4 E2B on LiteRT-LM (Mac). NVIDIA Jetson AGX Thor. Logitech Brio over GStreamer. Python 3 standard library on Thor and the Mac. `sounddevice` for the Mac mic.

**Status (honest):**
- *Working:* the full capture → teach → improve loop runs end to end in our test suite (134 tests) against a scripted Gemma stand-in.
- *Working, but not on Thor:* real Gemma 4 E2B, run on a CPU-only Linux box, gave valid output for every call type (observe, question, why, look, tool calling) once we fixed three bugs. LiteRT-LM ran Gemma 4 E2B on Linux CPU and answered 8 of 8 card questions correctly, at 1.6–2.3 s per answer.
- *Target, not yet verified:* Gemma 4 31B serving on Thor, the live Brio camera checks, voice over the USB-C link, speech-to-text on the Mac, LiteRT on the Mac itself, and end-to-end latency on Thor.

**Track:** Physical AI, Robotics and Embedded Devices (Embedded track).
**Also entering:** Best Use of LiteRT. Gemma 4 E2B on LiteRT-LM answers the new hire's questions from the captured card, fully offline, and says "not captured" when the card doesn't have the answer. *Target:* it also does the listening on the Mac (audio in, text out to Thor).

**Team:** Jake (software), [teammate name] (hardware).

**Repo:** https://github.com/jparker2006/Journeyman (Apache-2.0)

**Demo video:** [link, added before 8 PM]

**Vision:** Journeyman turns a senior tech's hands-on work into skills: for people today, robots tomorrow.

---

## Demo video plan (record once the live rig works, at most 3 min)
One take on a phone, aimed at the bench with the monitor in frame. Show live audio; no voice-over on the live parts.
1. **0:00–0:15** The rig: Thor, Brio, monitor, the Mac on its cable. Show the Wi-Fi icon off and `./jm doctor --offline` passing.
2. **0:15–1:15 Capture.** "Journeyman, start capture." The expert swaps a patch cable, narrating, and labels both ends without saying anything. Journeyman asks why. The expert answers, then says "I'm done", and the card appears on the monitor.
3. **1:15–2:15 Teach.** A second person says "teach me". They follow the steps, say "next", and the camera check is visible. They ask "why do I label both ends?" and get the expert's words back.
4. **2:15–2:40 Honesty.** "What torque do the screws need?" It answers "not captured", and the question shows up in the queue on the monitor.
5. **2:40–3:00** The vision line over the card on screen.

If live fails, record `./jm replay recordings/demo --speed 2` and label it on screen as **"recorded session replayed through the same pipeline"**. Never show a replay as live.

## Before 8 PM
Change a line from *target* to plain words only if someone on the team actually saw it work:
- [ ] `curl -s localhost:8000/v1/models` (or `:8001`) on Thor lists the 31B → "Gemma 4 31B serves on Thor"
- [ ] `./jm camera-test` on Thor saves a good Brio JPEG, and a teach "next" check gets the step right → "live camera checks"
- [ ] `./jm doctor --offline` exits 0 with Wi-Fi off → "the demo runs with Wi-Fi off"
- [ ] The headset over USB-C: a spoken line gets a spoken reply → "voice over the USB-C link"
- [ ] Mac ears (#41): a spoken line is transcribed on the Mac by LiteRT-LM → drop the LiteRT *target* sentence and say it listens
- [ ] `litert/answer.py` runs on the Mac → "LiteRT on the Mac"
- [ ] `./jm bench` on Thor → add the measured latencies, or leave latency out
- [ ] Fill in the teammate's name and the video link
