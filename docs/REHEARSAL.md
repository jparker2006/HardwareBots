# Dress rehearsal: the whole stack against a real Gemma 4

Thor wasn't up yet, so the full demo ran end to end in a sandbox with a real Gemma 4 as the model server.

- **When / where:** 2026-10-25, a Linux sandbox on ARM64, 4 CPU cores, no GPU. Not Thor.
- **Server:** Ollama 0.40.11, OpenAI-compatible `/v1`. **Model:** `gemma4:e2b` (Q4_K_M), rebuilt with `PARAMETER num_thread 4`.
- **Service:** `jm serve` with `JM_CAMERA=none`; `jm replay` pushes the utterances and frames over HTTP, the same path the headset and camera use.
- **Recording:** the rendered panel frames from `recordings/demo-frames` (PR #45), and `recordings/rehearsal/script.jsonl`: capture with an unexplained "label both ends", the expert's reason after a long pause, then teach with a stale frame (a skip), a "why?" question and the torque question that was never captured.

## Reproduce
```sh
curl -fsSL https://ollama.com/install.sh | sh          # needs zstd
ollama serve & ollama pull gemma4:e2b
printf 'FROM gemma4:e2b\nPARAMETER num_thread 4\n' > Modelfile && ollama create gemma4-e2b-cpu4 -f Modelfile
export JM_GEMMA_URL=http://localhost:11434/v1 JM_GEMMA_MODEL=gemma4-e2b-cpu4:latest JM_CAMERA=none JM_DATA=/tmp/jmdata
export JM_TIMEOUT_OBSERVE=240 JM_TIMEOUT_LOOK=90 JM_TIMEOUT_AGENT=90 JM_OBSERVE_EVERY=20   # CPU only; leave the defaults on Thor
./jm serve &
./jm doctor
./jm replay recordings/rehearsal --settle 240           # needs recordings/demo-frames (PR #45)
```

## Result: the full loop ran on real Gemma 4 E2B (run 4, all fixes in)
`replayed 21 lines: 5 steps saved, 2 questions asked, 1 open question logged`. In 13 minutes of wall time:

| Part | What happened |
|---|---|
| Capture | 5 steps saved from the frames. Journeyman asked **"Why did you point at the ports?"**. The expert's reply ("So the next tech can trace it without pulling every cable") was saved as an expert reason. "I'm done" read back "Saved 5 steps and 1 reason". |
| Teach | 5 steps given by voice. "done" ran a camera check each time. Step 1 was flagged twice as **not done** ("Hold on, find port by label isn't done yet"), and the slip was logged. |
| Q&A, captured | "why do I label both ends?" → "You label both ends so the next technician can trace the cable easily." That is the expert's saved reason, paraphrased. |
| Q&A, not captured | "what torque do the screws need?" → "That's not captured yet. I'll ask the expert next session." It was queued as an open question. |
| LiteRT-LM | `litert/answer.py` (E2B, CPU) on the card from this run: load 4.2 s, then 3.0 s and 1.2 s per answer. It said "isn't captured" to both questions, because the reason ended up on the wrong step (see below). |

## What broke, and the fixes (one commit and one test each)
1. **A slow observe lost its frames forever.** Each observe moved the frame window forward *before* it called Gemma. When the call timed out (60 s on CPU), those frames and that narration were never looked at again. Run 1 saved **0 steps**. Fix: a failed observe resends its frames next time. Test: `test_failed_observe_resends_its_frames_next_time`.
2. **Gaps expired before they could be asked.** A gap was timestamped when the observe *started*. Its 60 s TTL had already run out by the time a 140 s observe returned, so capture never asked "why?" (runs 2 and 3). Fix: the TTL starts when the gap is noticed. Test: `test_gap_from_a_slow_observe_is_still_asked`.
3. **The agent spoke a made-up reason.** "why do I label both ends?" with no saved reasons: Gemma called `speak` with an invented reason (spoken immediately, before the "not captured" check). Then a search hit on "label" in an unrelated step title counted as "found". Fix: during a question, `speak` calls are held until the check passes, and a "why" needs a matching item with a saved reason. Tests: `test_why_with_no_saved_reason_is_not_captured_and_not_spoken`, `test_held_speech_is_said_once_search_backs_it`.
4. **A server that drops audio counted as working.** On Ollama `/v1`, Gemma gets no audio and replies "I'm sorry, I cannot fulfill this request. I need an audio or video file to transcribe." (seen 3 of 3 times). `ears.py` would have passed that sentence on as what the person said, and `jm doctor` reported audio PASS. Fix: `ears.no_audio()` spots it. Ears say the NO_AUDIO line, and doctor FAILs with a hint. Tests: `test_ears_refusal_is_not_heard_as_speech`, `test_audio_dropped_by_server_fails`.

## Timings (CPU, 4 threads, E2B Q4, wall time seen by Journeyman)
| Call | Time |
|---|---|
| doctor: text (cold) / image / tool call | 17.5 s / 23.1 s / 1.4 s |
| observe, 1 / 2 / 4 frames | 39.6 s / 71 s / 141–143 s |
| look (teach "done" check, 1 new frame) | 29–33 s, including a 4 s filler line |
| question (capture "why?") | under 2 s warm. Once 15 s+ (timed out right after an observe, so the stock question was used) |
| why (attach answer) | 3.7 s |
| agent turn, answered from saved reason | 11.5 s |
| agent turn, not captured | 2.4–3.6 s |
| LiteRT-LM answer.py, E2B CPU | 4.2 s load, 1.2–3.0 s per answer |

On CPU the default timeouts are too short (observe 60 s, look 25 s). The rehearsal ran with `JM_TIMEOUT_OBSERVE=240 JM_TIMEOUT_LOOK=90 JM_TIMEOUT_AGENT=90 JM_OBSERVE_EVERY=20`. The script leaves long pauses so each answer arrives after its question. The 43 s demo pace only works if observe takes well under 10 s.

## Still weak (model quality, not fixed)
- **E2B misnames steps and reasons.** Saved steps included "Pointing at ports" and "Touching/manipulating a cable". The expert's labeling reason was attached to "Pointing at ports", because E2B never saw the labeling as its own step. The cue read "Pointing towards A1" while the finger was on A5.
- **E2B camera checks are unreliable.** The correct step 1 frame was judged "not done" twice. A stale frame was later judged "done". Run 2 called a correct "pull old cable" frame not done. The skip catch fires, but on E2B it can't be trusted as a judge.
- **The stock fallback question reads badly:** "Quick one: why touching/manipulating a cable?".
- **Ears on LiteRT-LM was not re-run here.** There is no speech WAV and no TTS in this sandbox. The earlier sandbox numbers in [litert/README.md](../litert/README.md) still stand.

None of this is Thor. Expect 31B on Thor's GPU to be both faster and better at judging. Measure it with `./jm bench --image <frame>.jpg` and the same replay.
