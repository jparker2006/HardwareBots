# Journeyman: 3-minute pitch (one presenter)

Jake gives the whole pitch alone: he is the expert, the speaker and the person at the keyboard. The judge is the new hire. This page is the spoken words; what to type and do, and the fallbacks, are in [DEMO.md](DEMO.md). The times below match its run sheet.

About 380 spoken words around the live demo. Lines marked **[target]** have a safe version to say until the live check in [Before you say it](#before-you-say-it) passes. Update this file after the first full run on Thor.

## 0:00–0:15 Hook
*Point at the Thor, the monitor, the Mac.*
> Every shop has one person who just *knows*. They label both ends of a cable before they unplug anything. Ask why and they shrug: "habit." When they retire, the habit leaves with them.
>
> This is Journeyman. It watches a senior tech work, asks about the steps they skip explaining, and teaches the next person. It runs on this Thor and this Mac. No cloud.

## 0:15–1:10 Capture (Jake is the expert)
> Journeyman, start capture.

Swap the cable, narrating as in DEMO.md, but **label both ends in silence**. At the pause Journeyman asks: *"You labeled both ends before plugging in. Why?"*
> So the next tech can trace it without pulling every cable.

> I'm done.

While it reads back the card, point at the monitor:
> That answer is on the card now, tagged "the expert said it." Anything the AI guessed stays unconfirmed until I say yes.

## 1:15–2:15 Teach (the judge's turn)
*Hand the judge the second cable.*
> You're the new hire. Say "teach me."

It gives one step at a time by voice and checks each one with the camera when the judge says "next". Nudge them to skip the labeling; it gets caught and named.

While the judge works (say it off-mic: in text mode that's automatic, in voice mode don't hold push-to-talk):
> Experts are bad at explaining their own work. By one estimate they leave out about 70% of their decisions. Interviewing them properly is expensive, so most shops never do it. And they're leaving: Boeing forecasts 710,000 new aircraft technicians needed by 2044, about two-thirds replacing retirees.

The judge asks **"Why do I label both ends?"** and gets back the expert's words from 1:05.

## 2:15–2:40 Honesty, then why Gemma, why local, why Thor
The judge asks **"What torque do the screws need?"**
> It says "not captured" and queues the question for my next session. It doesn't guess.

> **Why Gemma:** one open model family, split across the two boxes. On Thor, Gemma 4 31B looks through the camera, reasons about the work, and calls our tools to save steps and answer questions. **[target]** On the Mac, Gemma 4 E2B on LiteRT-LM is the ears: it turns speech into text, and only text crosses to Thor.
> *Until verified, say instead:* On the Mac, Gemma 4 E2B on LiteRT-LM can answer the new hire's questions from the card, offline.
>
> **Why local:** the footage shows customer sites and the shop's know-how. People recorded at work need to hear "it never leaves the building."
>
> **Why Thor:** a 31B model with vision, plus the camera and the knowledge base, live at the bench for a whole session.

## 2:40–3:00 Close
*Point at the Thor and the card.*
> Everything you saw ran on this Thor and this Mac. No cloud. The judge's skip and the torque question are on the card for next time.
>
> The buyer is the training director at an aircraft maintenance shop or a multi-branch contractor. Tools that turn expert video into work instructions already sell; IFS bought Poka for close to $200 million. They summarize video after the fact. Journeyman asks while the expert's hands are still on the work.
>
> Journeyman turns a senior tech's hands-on work into skills: for people today, robots tomorrow.

If the run fell back to the rehearsal card or backup video (DEMO.md fallbacks), drop the first sentence of the close and say: "That was the rehearsal card. The loop is the same."

## Before you say it
Each **[target]** line flips to its live wording only after Jake sees the check pass on the rig.

| Claim | Say until verified | Live check that flips it |
|---|---|---|
| Gemma 4 31B on Thor sees and reasons | "Designed for Gemma 4 31B on Thor"; or the backup video | `./jm doctor` lists the model, then one full capture → teach run on Thor |
| The camera checks each step | "It checks the step with the camera" only if the "next" check passed in the dry run | Dry run: a skipped label gets named |
| The Mac's ears are Gemma E2B on LiteRT-LM | The "until verified" line above; run the headset with `--text` | `python3 headset/headset.py --ears litert` on the Mac prints `you (gemma e2b, ...)` for a spoken line |
| "No cloud" | Always fine: the runtime only calls this machine or the local link | — |
| "It never leaves the building" / "offline" | "No cloud calls" | `./jm doctor --offline` passes with the uplink off |
| Any speed | Nothing | `./jm bench` on the rig; write the number in DEMO.md |

## Numbers we may say, and their labels
| Claim | Source and label |
|---|---|
| Experts omit ~70% of their decisions | Yates, Feldon and Clark, AERA workshop material ([bepress](https://works.bepress.com/david_feldon/115)). Approximate, not a primary study. Say "by one estimate". |
| 710,000 new aircraft technicians by 2044, ~2/3 replacing retirees | Boeing 2025 Pilot & Technician Outlook ([Aviation Business News](https://www.aviationbusinessnews.com/industry-news/boeing-forecasts-710000-new-aircraft-technicians-needed-by-2044)) |
| ~$27B/yr lost to unavailable aircraft and MRO inefficiency | BCG, 2026 ([BCG](https://www.bcg.com/publications/2026/older-aircraft-too-few-technicians-the-aerospace-fix)). Methodology not reviewed. Q&A only. |
| IFS bought Poka for close to $200M | Price undisclosed. CEO Darren Roos called it "not far off" $200M, per [Verdantix](https://www.verdantix.com/insights/blogs/ifs-acquires-poka-to-tackle-core-industry-hurdles). Deal announced June 2023 ([IFS](https://www.ifs.com/en/insights/news/ifs-acquires-poka)). |
| 79% of respondents said their last data-center outage involved human error | Uptime Institute 2021 survey, as reported secondhand ([Betanews](https://betanews.com/2021/09/14/data-center-capacity-challenges/)). Uptime's own release: 42% had a human-error outage in 3 years, and 57% of those cited staff not following procedure ([Uptime](https://connect.uptimeinstitute.com/about-ui/press-releases/uptime-institute-releases-3rd-annual-outage-analysis)). Q&A only. |

## Do not say
- That it is proven on Thor, or any latency number, until measured (`./jm bench`).
- That LiteRT runs on the Mac or does the listening, until the ears check above passes there. LiteRT-LM with Gemma 4 E2B is verified only on a Linux CPU so far ([litert/README.md](../litert/README.md)).
- "Air-gapped", unless `./jm doctor --offline` passes.
- "Impossible without Thor."
- That it captures all tacit knowledge, cuts training time, or reduces callbacks.
- The 70% figure as a measured fact.
- That it is safe for energized equipment. The demo cable is unpowered.
- Anyone's employer. Names only.
