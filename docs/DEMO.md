# Journeyman: demo run sheet (one presenter, 3 minutes)

Jake runs everything alone: he is the expert, the speaker and the person at the keyboard. The judge is the new hire. The pitch words are in [PITCH.md](PITCH.md); this page is what to type and do.

Addresses: Thor is `192.168.55.1` over the USB-C link, or `192.168.3.131` on the venue Wi-Fi. The headset tries both, in that order.

## Before the judges arrive (T-20 min)

**Thor** (in the repo folder):
```sh
./jm camera-test                     # before serve takes the Brio: saves data/camera-test.jpg; hands and cable ends in frame?
scripts/thor-up.sh --only-31b        # vLLM Gemma 4 31B NVFP4 on :8001, jm doctor, then jm serve with the Brio
source /tmp/journeyman/env.sh        # so later ./jm commands use the same settings
./jm doctor                          # Gemma model listed, service running, camera source shown
```
- `--only-31b` because E4B (the audio model) isn't on Thor and can't be downloaded at the venue. Without it Gemma can't hear, so the headset runs with `--text`. If the E4B is cached on Thor, run `scripts/thor-up.sh` with no option, and voice works.
- `scripts/thor-up.sh --dry-run` prints every command without running anything. `scripts/thor-down.sh` stops it all.
- `jm serve` logs to `/tmp/journeyman/serve.log`. Open `http://localhost:3000` full screen on the monitor.

**Seeded backup card** (prove it works, then clear it for the live capture):
```sh
./jm seed recordings/demo            # the rehearsed 4-step cable-swap card, all confirmed; the monitor shows it
./jm card cable-swap                 # check it, then clear it:
kill $(cat /tmp/journeyman/serve.pid) && ./jm reset && scripts/thor-up.sh --only-31b --no-doctor
```
`./jm reset` archives `data/` to `data/archive/<time>/` (nothing is deleted) and refuses while `jm serve` runs. `./jm seed` refuses if the card already has steps. Re-running `thor-up.sh` reuses the running model container, so it only restarts `jm serve` (a few seconds).

**Mac** (in the repo folder; text mode needs nothing installed):
```sh
python3 headset/headset.py --text    # type; Journeyman's replies come out of the Mac speaker
python3 headset/headset.py           # voice, only if the E4B is up (needs: pip3 install sounddevice numpy)
python3 headset/headset.py --ptt     # voice in a loud room: Enter starts, Enter stops
```
Pin a fixed address with `--thor http://192.168.3.131:3000` if the auto-pick picks wrong. It should print `journeyman status: idle`.

**Bench:** an unpowered switch or patch panel, 2 patch cables, tape and a pen. Brio overhead, both hands in frame, no glare. Nothing energized. One full dry run in the room.

**Last check:** `./jm card cable-swap` shows an empty card, the monitor shows idle, the headset is connected.

## The 3 minutes

In `--text` mode, Jake says each line out loud and types it as he goes (short lines; the judge types theirs, or Jake types for them). In voice mode, just talk.

| Time | Do | Say / type | Expect |
|---|---|---|---|
| 0:00 | Point at the Thor, the monitor, the Mac | Hook: "Every shop has one person who just knows... It runs on this Thor. No cloud." | |
| 0:15 | | **"Journeyman, start capture."** | Monitor: capture mode |
| 0:20 | Touch the labeled port | **"First I find the port by its label, not by counting."** | A step on the card |
| 0:35 | **Label both ends of the new cable. Say nothing.** Big and slow, in frame. | (silence) | |
| 0:50 | Plug it in, leave a loop | **"Now it goes in, and I leave a little loop of slack."** | Another step on the card |
| ~1:00 | Wait at a pause | | Journeyman asks: *"You labeled both ends before plugging in. Why?"* |
| 1:05 | | **"So the next tech can trace it without pulling every cable."** | The reason lands on the card, tagged "expert said" |
| 1:10 | | **"I'm done."** | It reads back the card |
| 1:15 | Hand the judge the second cable | "You're the new hire. Say 'teach me'." | |
| 1:15 | Judge | **"Teach me."** | Step 1 by voice |
| 1:25 | Judge does a step, nudge them to skip the labeling | **"Next."** | Camera check; the skipped labeling gets caught and named |
| 1:55 | Judge | **"Why do I label both ends?"** | The answer, in Jake's words from 1:05 |
| 2:15 | Judge | **"What torque do the screws need?"** | *"That's not captured yet. I'll ask the expert next session."* The question is queued on the monitor |
| 2:40 | Point at the Thor and the card | Close: "Everything you saw ran on this Thor. No cloud. The judge's skip and the torque question are now on the card for next time." | |

## Fallback lines
| Failure | Say | Do |
|---|---|---|
| **Slow answer** (>10 s) | "It's thinking on the box. There's no cloud to lean on." | Wait up to ~20 s, then carry on and come back to it. |
| **No "why?" after the silent step** | "It asks at most once every 20 seconds, and only at a pause." | Pause and redo the labeling in frame. Still nothing: type **"Journeyman, what did I just do that I didn't explain?"** |
| **Misheard speech** (voice mode) | "The room's loud. I'll type it. Same pipeline, same model." | Ctrl-C the headset, run `python3 headset/headset.py --text`. |
| **Camera miss** (passes or fails a step wrongly) | "It didn't see that. That's why it asks instead of assuming." | Hold the work closer to the Brio and say "next" again. |
| **Odd answer** | "Anything it infers stays unconfirmed until the expert says yes." | Show the card: `./jm card cable-swap`. |
| **Capture saved an empty card** | "Here's the card we captured in rehearsal. Teaching works the same." | `./jm seed recordings/demo` (live, no restart), then "teach me". |
| **Capture saved a partial or messy card** | Same line. | `kill $(cat /tmp/journeyman/serve.pid) && ./jm reset --seed recordings/demo && scripts/thor-up.sh --only-31b --no-doctor` (~15 s), then "teach me". |
| **Headset link drops** | "The Mac is just the mic and speaker. Thor does the rest." | Ctrl-C and rerun the headset; or `--thor http://192.168.3.131:3000`. |
| **`jm serve` died** | "Restarting the service; the card is saved on disk." | `scripts/thor-up.sh --only-31b --no-doctor`; `tail /tmp/journeyman/serve.log`. |
| **Gemma or Thor down** | "Here's the same loop from rehearsal." | Play the backup video. |

## Honesty
- "No cloud": the runtime only talks to this machine or the local link ([local_http.py](../journeyman/local_http.py)). If the Mac reaches Thor over venue Wi-Fi, Thor may still have internet; `./jm doctor --offline` passes only with the uplink off. Say "no cloud calls", not "air-gapped", unless it passes.
- No latency numbers unless measured: `./jm bench` on the rig, then fill in here: ______.
