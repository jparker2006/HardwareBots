# Journeyman: architecture (draft v3, 2026-10-24 15:05 PT)

A voice-first, fully local instructor for hands-on work. It watches and listens while a senior technician works, asks about the steps they don't explain, saves what it learns, then teaches the next person by voice and watches them work too. It runs on a Jetson Thor, with a Mac acting as the headset. No cloud.

**Track:** Physical AI, Robotics and Embedded Devices (Embedded). Stretch: Best Use of LiteRT.
**Status words:** *Built*, *Proposed*, *Unverified*. Everything below is Proposed until a checkpoint proves it.

## Design rules (borrowed from OpenWiz, Cloudroom core/app and Lexicon)
1. **One core owns state; every surface is a thin client of the same operations.** The voice headset, the `jm` CLI, the monitor UI and the Gemma agent all call one operation set. There are no UI-only or voice-only operations. *(OpenWiz controller; Cloudroom "UI, SDK and CLI together")*
2. **Agent-first: the CLI is the tool surface.** Each Gemma tool *is* a `jm` command. One registry generates the function-calling schemas and the CLI help. *(room-cli is first-class for users and agents)*
3. **Append-only event journal is the source of truth.** Knowledge, sessions and trainee progress are projections rebuilt by replay. Events are fsynced before subscribers hear them. Guarded status moves make replayed or racing events no-ops. *(Cloudroom journal and replay; Lexicon History `apply`)*
4. **The same pipeline runs headless on recordings.** `jm replay` feeds a recorded session (WAV + frames) through the real pipeline with no mic or camera. Used for prompt iteration, tests and the backup demo. *(Lexicon `lexicon-eval` / HeadlessSystem)*
5. **Memory is data, not instructions.** Every knowledge item carries provenance (expert said / AI inferred / trainee event), a confirmed flag and versions. Nothing in memory or a transcript can grant a tool permission. *(OpenWiz principles 6 and 8; Lexicon rules with a source field: added or learned)*
6. **"Completely local" is enforced at one chokepoint.** All model and network calls go through one `local_http` client that refuses any host other than localhost or the USB-C link. `jm doctor --offline` proves it. *(Lexicon `CloudAccess`)*
7. **Bounded loops and honest output.** At most 4 tool calls per agent turn and 1 question per ~20 s. Changing *confirmed* knowledge needs the expert's spoken yes. Unknown means "not captured", never a guess. Lists report freshness. *(OpenWiz bounded loops, policy gates, honest lists)*
8. **The conductor orders events and holds no durable state.** *(Lexicon Conductor)*
9. **Contracts live in one place.** JSON schemas for the headset protocol and tools sit in `contracts/`. *(Cloudroom contract packages; OpenWiz `contracts/`)*
10. **Budgets are measured, not assumed.** `jm bench` reports latency for each stage. *(Lexicon budgets)*

## Shape
```
                      ┌────────────────────── THOR ──────────────────────────────┐
 MAC headset          │  journeymand (core)                                      │
 ┌───────────────┐    │   ├─ conductor   orders events, modes, turn-taking       │
 │ mic → VAD→WAV │─ws─┼──▶├─ ears        Gemma E4B: audio → text                 │
 │ say ◀─ text   │◀───┼───├─ eyes        keyframe buffer + Gemma vision (`look`) │
 └───────────────┘    │   ├─ agent       Gemma + tools (= jm operations)         │
 external camera ─USB─┼──▶├─ journal     events.jsonl (append-only, fsync)       │
                      │   ├─ projections knowledge.sqlite + procedures/*.md      │
 jm CLI (human/agent)─┼──▶├─ ops         one operation set, one registry         │
                      │   └─ ui          http :3000 → HDMI monitor (read-only)   │
                      │  vLLM :8000 E4B   (:8001 26B-A4B later)                  │
                      └──────────────────────────────────────────────────────────┘
```

## One path tree
```
/procedures/<procedure>/<step>      steps, reasons, consequences, clips, confirmed?
/sessions/<id>                      capture or teach session: transcript, keyframes, events
/trainees/<id>                      progress, mistakes, questions
/questions/open                     not captured yet; asked at the expert's next session
/lessons                            the agent's notes to itself (data, not instructions)
```

## Operations (CLI commands = agent tools)
| Kind | Commands | Agent tool? |
|---|---|---|
| **List** (never wakes anything) | `jm ls [path]`, `jm status` | `search_knowledge` wraps `ls` + search |
| **Inspect** | `jm show <path>`, `jm card <procedure>`, `jm search "<query>"` | yes: `search_knowledge`, `get_procedure` |
| **Sense** | `jm look --seconds N "<question>"` | yes: `look` |
| **Speak** | `jm say "<text>"` | yes: `speak` |
| **Change** | `jm save-step`, `jm save-why`, `jm log-question`, `jm flag-disagreement`, `jm log-trainee`, `jm save-lesson`, `jm confirm <path>@rN` | yes, except `confirm`: only the expert's spoken yes or a human runs it |
| **Session** | `jm session start capture|teach`, `jm session end` | voice keywords map to these |
| **Dev** | `jm replay <recording>`, `jm bench`, `jm doctor --offline` | no |

Every change names the revision it saw (`@rN`). A stale change is refused with "changed since you looked".

## Events (one list)
`utterance_heard`, `frame_kept`, `gap_noticed`, `question_asked`, `answer_attached`, `step_saved`, `why_saved`, `item_confirmed`, `item_corrected`, `question_logged`, `disagreement_flagged`, `trainee_step_done`, `trainee_mistake`, `lesson_saved`, `session_started`, `session_ended`.

## The loop
- **Capture (expert):**
  - VAD utterance → `utterance_heard`.
  - Frames → `frame_kept`.
  - At each pause or 10 s tick, the agent calls `look` and compares what was done with what was said → `gap_noticed`.
  - Silence gate plus rate limit → `speak` a question → the next utterance becomes `answer_attached` → `save_why`.
- **Teach (new hire):**
  - `get_procedure` → `speak` the step → `look` to check it → `trainee_step_done` or `trainee_mistake`.
  - "Why?" → `search_knowledge` → answer in the expert's words, or "not captured" plus `log_question`.
- **Improve:**
  - Mistakes add warnings to steps. Open questions queue for the expert. Expert corrections become `item_corrected` plus a lesson.

## Models
- Start with **one Gemma 4 E4B** (audio + image + tools) for ears, eyes and agent.
- Add **26B-A4B** for eyes and agent if latency and memory allow (*Unverified*).
- vLLM flags: `--enable-auto-tool-choice --tool-call-parser gemma4`. Fallback if tool calls are flaky: one constrained JSON action parsed by code.

## Repo layout
```
journeyman/        core: conductor, ears, eyes, agent, journal, projections, ops, ui
  ops.py           the one operation registry (CLI + agent tool schemas come from here)
  local_http.py    the only network client: localhost / USB-C link only
headset/           Mac client: mic → VAD → WAV over ws; `say` playback; mic mute while speaking
contracts/         JSON schemas: headset protocol, tools, events
recordings/        fixture sessions for `jm replay` (synthetic or recorded for the test)
tools/             gemma_probe.py
docs/              ARCHITECTURE.md, DECISIONS.md
```

## Not today
Rust core, multi-user memory, PACP-style agent connections, LoRA retraining on the knowledge base, a desktop app.

## Known risks
- **Gemma audio latency and quality on Thor:** unmeasured.
- **Spotting unexplained actions at about 1 fps:** unverified. Use big, deliberate actions in the demo.
- **E4B tool-call reliability:** unverified.
- **Echo:** the mic hears `say` output. Use half-duplex: mute the mic while speaking.
- **Venue noise:** keep the Mac mic close.
