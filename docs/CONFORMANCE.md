# Real Gemma 4 conformance run

What happened when Journeyman talked to a real Gemma 4 instead of `tests/fake_gemma.py`.

- **When / where:** 2026-10-24, a Linux sandbox on ARM64 with 4 CPU cores and no GPU. Not Thor.
- **Server:** Ollama 0.40.11 (llama.cpp runner), OpenAI-compatible `/v1`.
- **Model:** `gemma4:e2b` (Q4, 4.6 GB on disk), rebuilt as `gemma4-e2b-cpu4` with `PARAMETER num_thread 4`.
- **Images:** two CC photos of switches with patch cables from Wikimedia Commons ("Netgear Gigabit Switch 5-port.jpg", CC BY-SA 3.0; "Raspberry Pi 4 on a network switch.jpg", CC0), 500 px wide. They are not committed.
- **Session:** a capture + teach script (start capture, three narrated steps, an answer, done, teach me, done, two questions), run with `jm eval --all`. Each call type was also driven on its own through `Core` with the raw replies logged.

## Reproduce

```sh
curl -fsSL https://ollama.com/install.sh | sh          # needs zstd
ollama serve & ollama pull gemma4:e2b
printf 'FROM gemma4:e2b\nPARAMETER num_thread 4\n' > Modelfile && ollama create gemma4-e2b-cpu4 -f Modelfile
export JM_GEMMA_URL=http://localhost:11434/v1 JM_GEMMA_MODEL=gemma4-e2b-cpu4:latest
./jm doctor
./jm bench --runs 3 --image switch.jpg
./jm eval <recording dir with script.jsonl + frames/> --all
```

## What worked

| Call | Result |
|---|---|
| observe (2 frames, JSON) | Valid JSON every time. Steps were plausible, sometimes misnamed ("Connect power cable" for an Ethernet cable). It flagged one unexplained action (confidence 0.9). Frame ids were real. |
| question | One short spoken question, ends in "?", no wrapping or markdown. |
| why (JSON) | Valid JSON. Maps the answer to a step number. See bug 2 for the hard part. |
| look (1 frame, JSON) | Valid `{"answer", "yes", "frame_id"}`; `yes: true` for a cable that was visibly plugged in. |
| agent (tools) | Ollama returns parsed `tool_calls` with JSON-string arguments. The model calls `search_knowledge`, then `log_question` when nothing is found, then gives a short spoken reply. The loop and the "not captured" guard behaved as designed. |
| doctor / bench | Text, image and tool calling PASS after fix 3. |

## What broke, and the fixes

1. **Gemma 4 thinks by default on Ollama, so every reply came back empty** (`content: ""`, `finish_reason: length`, text in `reasoning`). Every Journeyman call has a small `max_tokens`, so all of them failed. `chat_template_kwargs.enable_thinking=false` and `think:false` are ignored by Ollama `/v1`; only `reasoning_effort: "none"` works.
   Fix: `Gemma.chat` now sends `reasoning_effort: "none"` alongside the existing `chat_template_kwargs`. It is an OPTIONAL field: if a server rejects it by name, it is dropped for good. Tests: `tests/test_conformance.py`.
2. **"why" accepted plain narration as the reason.** Right after a question, the expert said "Now I pull the old cable straight out". E2B returned that sentence as `why` every time. Both non-reason test answers were saved as reasons. A stronger prompt sentence did not help.
   Fix: the WHY prompt asks for `"gives_reason": true|false`, and `false` keeps the question open. Live: 9 of 10 right on the same 5 answers. In the replay the narration was rejected and the question stayed open. Test: `test_narration_echoed_as_why_is_not_an_answer`.
3. **`jm doctor` / `jm bench` reported PASS on empty answers**, and "tool calling: answered in text: ''" (a false WARN), because they built their own request and hit bug 1.
   Fix: `doctor.chat` goes through `Gemma.chat`. Test: `test_doctor_probes_ask_for_no_thinking`.

## Not fixed (reported)

- **Ollama `/v1` drops `input_audio`.** The model answers "I need an audio file…", so `jm doctor` audio PASS is wrong on Ollama, and `ears.py` would transcribe nothing. Use vLLM with E2B/E4B for Gemma audio, or transcribe on the Mac.
- **Ollama on a small CPU box picks 64 threads** (it sees the host cores, not the cgroup). A 4-token reply took 3 minutes. `num_thread 4` gave 0.2–0.5 s.
- **E2B judgement is weak** (model quality, not format): misnamed steps; once accepted "I leave a little loop of slack" as the reason; "what step am I on?" in capture mode called `look` rather than `search_knowledge`, so the guard answered "not captured". Expect 31B / E4B on Thor to do better. Unverified.
- `jm eval` uses virtual time, but `Eyes.recent()` uses wall time. On a slow model, frames ingested in teach mode look older than 10 s, so the teach "done" check skipped `look` ("I can't quite tell"). This only affects eval; live frames arrive every second.

## Per-call latency (CPU, 4 threads, E2B Q4; wall time from Journeyman)

| Call | Typical | Notes |
|---|---|---|
| text, short (`Say OK`) | 0.2–0.3 s | |
| question | 1.2–1.4 s | |
| why (JSON) | 2.2–4.1 s | |
| agent turn, first call | 0.6 s warm, 24 s cold | cold = first prompt with system + tool schemas |
| agent turn, total (3 calls) | 3–4 s warm | search → log_question → reply |
| look, 1 new image | 22 s | 0.9 s when the same image is re-sent (prompt cache) |
| observe, 2 new images | 47–53 s | longer than the 10 s observe interval; observes queue behind each other |
| tool probe (doctor) | 0.4–1.9 s | |
| cold model load | about 12 s | after the thread fix |

None of this is Thor. Thor's GPU should be much faster; measure it there with `./jm bench --image <frame>.jpg`.
