# Journeyman: demo run sheet (3 minutes)

Roles: **Expert** does the capture. **Speaker** talks to the judges and runs the laptop. The **judge** is the new hire. The spoken words are in [PITCH.md](PITCH.md).

## Setup checklist (T-15 min)
- [ ] **Cables:** Thor power. Thor HDMI to the monitor. Mac to Thor over USB-C (check that `ping 192.168.55.1` works from the Mac). Brio to Thor over USB.
- [ ] **Brio mount:** overhead or high front, aimed at the work area, with both hands and the cable ends in frame. Even light and no glare. Check the live frame on the monitor.
- [ ] **Props:** a small unpowered switch or patch panel, 2 patch cables, a label maker or tape and a pen. Nothing energized.
- [ ] **Mic:** the Mac within ~30 cm of whoever is talking, mic pointed at them. Speaker volume up. The mic is muted while Journeyman speaks (half-duplex), so don't talk over it.
- [ ] **Wi-Fi off** on Thor and the Mac.
- [ ] **Thor:** Gemma is serving (`curl -s localhost:8000/v1/models`), then `JM_GEMMA_URL=http://localhost:8000/v1 ./jm serve`.
- [ ] `./jm doctor --offline` exits 0. It should list the Gemma model, show Journeyman running with the camera source, and show the internet as unreachable.
- [ ] **Mac:** `python3 headset/headset.py --thor http://192.168.55.1:3000`. Keep a second terminal ready with `--text`.
- [ ] **Clean state:** start with a fresh `JM_DATA` folder, or one with only the rehearsed card. Know which one you're using.
- [ ] **Backup:** the recorded video is open in a tab, and `./jm replay recordings/demo --speed 2` has been tested.
- [ ] One full dry run in the room, to check noise and lighting.

## Beats
| Time | Who | Do | Say / expect |
|---|---|---|---|
| 0:00 | Speaker | Point at the Thor, the monitor and the Wi-Fi icon | Hook (PITCH.md) |
| 0:20 | Speaker | — | Problem (PITCH.md) |
| 0:45 | Expert | — | "Journeyman, start capture." The monitor shows capture mode. |
| 0:50 | Expert | Find the port by its label | "First I find the port by its label, not by counting." |
| 0:58 | Expert | Pull the old cable straight out | "Now I pull the old cable straight out." |
| 1:05 | Expert | **Label both ends of the new cable. Say nothing.** Make it big and slow, in front of the camera. | (silence) |
| 1:12 | Expert | Plug in and leave a slack loop | "New cable goes in, and I leave a little loop of slack." |
| ~1:15 | Journeyman | — | Expected question: *"You labeled both ends before plugging in. Why?"* (target: within ~10–20 s) |
| 1:20 | Expert | — | "So the next tech can trace it without pulling every cable." |
| 1:25 | Expert | — | "I'm done." Journeyman reads back the saved steps. The monitor shows the card. |
| 1:30 | Speaker | Hand the cable to the judge | "Your turn. You're the new hire. Say 'teach me'." |

## The judge's turn (1:30–2:15)
1. The judge says **"Teach me."** Journeyman speaks step 1.
2. The judge does it and says **"Next."** Journeyman checks with the camera, then gives the next step or names the skipped one.
   - If the judge skips the labeling, even better: Journeyman should catch it. Let it happen.
3. Prompt the judge: "Ask it why." The judge asks **"Why do I label both ends?"** The answer comes in the expert's words.
4. Prompt: "Now ask something we never covered." For example **"What torque do the screws need?"** Expected: *"That's not captured yet. I'll ask the expert next session."*
5. Speaker: point at the monitor. The open question is queued.

Then the close: why Gemma, local and Thor, the business case and the vision line (PITCH.md).

## Fallback lines
| Failure | Say | Do |
|---|---|---|
| **Slow answer** (>10 s of silence) | "It's thinking on the box. There's no cloud to lean on. On the monitor you can see the event log filling in." | Wait up to ~20 s. Then move on and come back to it. |
| **No question after the silent step** | "It didn't ask yet. It asks at most once every 20 seconds, and only at a pause." | Pause, then repeat the labeling slowly in frame. Still nothing? Ask: "Journeyman, what did I just do that I didn't explain?" |
| **Misheard speech** | "The room's loud. I'll type it. It's the same pipeline, the same model." | Use the `--text` headset terminal. |
| **Camera miss** (passes or fails a step wrongly) | "It didn't see that. That's why it asks instead of assuming, and why the expert confirms the card." | Hold the work closer to the Brio and say "next" again. |
| **Wrong or odd answer** | "Everything it infers is marked unconfirmed until the expert says yes. Here's the card." | Show the card: `./jm card cable-swap`. |
| **Gemma or Thor down** | "Here's the same pipeline running a recorded session." | `./jm replay recordings/demo --speed 2`, or play the backup video. |
| **Headset link drops** | "The Mac is just the mic and speaker. Thor does the rest." | Restart `headset.py`, or use `--text`. |

## After the demo
- Don't claim latency numbers we didn't measure. If asked, run `./jm bench` live or say "we measured X in rehearsal" (fill X in after the run).
