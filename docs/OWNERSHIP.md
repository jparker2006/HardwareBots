# Who owns which files (parallel agents)

Each ticket owns its files. Touch a file you don't own only if you must, keep it minimal, and say so in your report.

| Ticket | Owns |
|---|---|
| Doctor / setup checklist | `journeyman/doctor.py` (new), the `doctor` and `bench` parts of `journeyman/cli.py`, `tools/` |
| Record & replay | `journeyman/recorder.py` (new), the `replay` part of `journeyman/cli.py`, recording hooks in `journeyman/server.py`, `recordings/` |
| Q&A and tool calling | `journeyman/agent.py`, `journeyman/gemma.py` |
| Teach mode | `journeyman/teach.py`, teach entries in `COMMANDS` in `journeyman/conductor.py` |
| Capture mode and prompt eval | `journeyman/capture.py`, `journeyman/eval.py` (new) |
| Monitor UI | `journeyman/ui/` |
| Mac headset | `headset/` |
| Pitch and submission | `README.md`, `docs/PITCH.md`, `docs/DEMO.md` |
| LiteRT on the Mac | `litert/` (new) |

Shared, change only with care: `journeyman/ops.py`, `journeyman/state.py`, `journeyman/journal.py`, `journeyman/core.py`, `tests/fake_gemma.py`.
Every branch must keep `python3 -m unittest discover -s tests -t .` green.
