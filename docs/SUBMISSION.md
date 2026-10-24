# Submission draft (AGI House event site)

Save the draft by **7:00 PM PT** (finalists are picked from drafts). Submit by **8:00 PM PT**. Before the final submission, update every *(target)* line with what we actually saw on Thor.

**Title:** Journeyman

**Tagline:** A fully local AI instructor that learns a senior tech's hands-on work by asking "why?", then teaches the next person by voice.

**Description (~150 words):**
Every shop has a senior tech whose know-how was never written down. When they explain a job, they leave out most of their decisions. Journeyman watches and listens while the expert works, with a webcam on an NVIDIA Jetson AGX Thor and a Mac as the mic and speaker. It notices the steps they did but didn't explain and asks about them on the spot: "You labeled both ends before plugging in. Why?" The answers go onto a procedure card, and each one records whether the expert said it or the AI guessed it. Then Journeyman teaches a new hire by voice, one step at a time. It checks each step with the camera, answers "why?" in the expert's own words, and says "not captured" instead of guessing. The question it couldn't answer goes into a queue for the expert. It runs on Gemma 4, entirely on-device, with no cloud.

**How it works:**
- **Ears:** the Mac headset sends speech over the USB-C link. Gemma 4 (E4B) transcribes it on Thor.
- **Eyes:** a Brio webcam on Thor keeps keyframes. Gemma answers questions about them (`look`).
- **Agent:** Gemma calls tools, and each tool is a `jm` CLI command: `save_step`, `save_why`, `search_knowledge`, `log_question`, `speak`. At most 4 tool calls per turn, and at most one question every 20 s.
- **Memory:** an append-only event log is the source of truth. The knowledge base and Markdown procedure cards are rebuilt from it. Memory is data, not instructions. Changing a confirmed item needs the expert's spoken yes.
- **Local only:** every network call goes through one client that refuses any host other than localhost and the USB-C link. `jm doctor --offline` checks this.
- **Headless replay:** `jm replay` runs recorded sessions through the same pipeline, for tests and as the demo backup.

**Tech stack:** Gemma 4 (E4B; 26B-A4B for vision if memory and latency allow, *target*). vLLM (Jetson Thor container, OpenAI-compatible, Gemma 4 tool-call parser). NVIDIA Jetson AGX Thor. Logitech Brio. Python 3 standard library on Thor and the Mac. `sounddevice` for the Mac mic and macOS `say` for speech.

**What's local:** everything. The models, transcription, vision, the agent, the knowledge base, the event log and the monitor UI all run on Thor. The Mac is only a mic and speaker, connected by cable. The demo runs with Wi-Fi off.

**Status (update before 8 PM):** the full capture → teach → improve loop passes end-to-end tests against a scripted stand-in for Gemma. Live Gemma on Thor, camera checks and voice latency are the *target*, not yet verified.

**Track:** Physical AI, Robotics and Embedded Devices (Embedded track). Add "Best Use of LiteRT" only if the LiteRT piece runs.

**Team:** Jake (software), [teammate name] (hardware).

**Repo:** https://github.com/jparker2006/HardwareBots (Apache-2.0)

**Vision:** Journeyman turns a senior tech's hands-on work into skills: for people today, robots tomorrow.
