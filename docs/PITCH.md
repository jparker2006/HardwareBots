# Journeyman: 3-minute pitch

Spoken script, about 420 words plus the live demo. Timings are targets. Everything marked *(planned)* has to be checked on the live rig before we say it on stage. Update this file after the first full run on Thor.

## 0:00–0:20 Hook
> Every shop has one person who just *knows*. They label both ends of a cable before they unplug anything. They leave a loop of slack. Ask them why and they shrug: "habit." When they retire, the habit leaves with them.
>
> This is Journeyman. It watches a senior tech work, asks about the steps they didn't explain, and teaches the next person. It runs entirely on this Jetson Thor. Wi-Fi is off.

## 0:20–0:45 Problem
> Experts are bad at explaining their own work. Researchers who study expert interviews estimate that experts leave out most of their decisions, around 70% by one estimate. The fix, a trained analyst interviewing the expert, is expensive, so most shops never do it.
>
> Meanwhile the experts are leaving. Boeing forecasts 710,000 new aircraft technicians needed by 2044, and about two-thirds of them replace retirees. BCG puts the cost of unavailable aircraft and inefficient maintenance at around $27 billion a year.

## 0:45–2:15 Live demo (see [DEMO.md](DEMO.md) for the run sheet)
1. **Capture, 0:45–1:25.** The expert says "Journeyman, start capture" and swaps a patch cable. They narrate, but they label both ends without saying anything.
   > Watch: I'm not going to explain one of these steps.

   Journeyman asks out loud: *"You labeled both ends before plugging in. Why?"* The expert answers. They say "I'm done" and it reads back the card.
   > That answer is now on the card, tagged "the expert said it." Anything the AI guessed is marked unconfirmed.
2. **Teach, the judge's turn, 1:25–2:05.** The judge says "Teach me". Journeyman gives one step at a time by voice and checks each one with the camera when the judge says "next". The judge asks "Why do I label both ends?" and gets the expert's own words back.
3. **Honesty, 2:05–2:15.** The judge asks something the expert never covered, e.g. "What torque do the screws need?"
   > It says "not captured", and it queues the question for the expert's next session. It doesn't guess.

## 2:15–2:40 Why Gemma, why local, why Thor
> **Why Gemma:** one open model family does the ears, the eyes and the reasoning. Gemma 4 hears the expert, looks at the camera, and calls our tools to save steps and answer questions.
>
> **Why local:** the footage shows customer sites and the shop's know-how, which is its competitive edge. People being recorded at work need to hear "it never leaves the building." Hangars and plant floors often have bad signal anyway.
>
> **Why Thor:** it keeps the ears, the eyes, the agent and the knowledge base running at once, live, for a whole session. A laptop could run a slower, one-model version. Thor runs the whole loop at the bench.

## 2:40–3:00 Business case and vision
> The buyer is the training director at an MRO shop or a multi-branch contractor. People already pay for this kind of software: IFS bought Poka, which turns expert video into work instructions, for close to $200 million, according to IFS's CEO. Those tools summarize video after the fact. Journeyman asks its questions during the work, while the expert's hands are still on it.
>
> Journeyman turns a senior tech's hands-on work into skills: for people today, robots tomorrow.

## Numbers we may say, and their labels
| Claim | Source and label |
|---|---|
| Experts omit ~70% of their decisions | Yates, Feldon and Clark, AERA workshop material ([bepress](https://works.bepress.com/david_feldon/115)). Approximate, not a primary study. Say "by one estimate". |
| 710,000 new aircraft technicians by 2044, ~2/3 replacing retirees | Boeing 2025 Pilot & Technician Outlook ([Aviation Business News](https://www.aviationbusinessnews.com/industry-news/boeing-forecasts-710000-new-aircraft-technicians-needed-by-2044)) |
| ~$27B/yr lost to unavailable aircraft and MRO inefficiency | BCG, 2026 ([BCG](https://www.bcg.com/publications/2026/older-aircraft-too-few-technicians-the-aerospace-fix)). Methodology not reviewed. |
| IFS bought Poka for close to $200M | Price undisclosed. CEO Darren Roos called it "not far off" $200M, per [Verdantix](https://www.verdantix.com/insights/blogs/ifs-acquires-poka-to-tackle-core-industry-hurdles). Deal announced June 2023 ([IFS](https://www.ifs.com/en/insights/news/ifs-acquires-poka)). |
| 79% of respondents said their last data-center outage involved human error | Uptime Institute 2021 survey, as reported secondhand ([Betanews](https://betanews.com/2021/09/14/data-center-capacity-challenges/)). Uptime's own release: 42% had a human-error outage in 3 years, and 57% of those cited staff not following procedure ([Uptime](https://connect.uptimeinstitute.com/about-ui/press-releases/uptime-institute-releases-3rd-annual-outage-analysis)). Use this one only in Q&A. |

## Do not say
- That it is proven on Thor, or any latency number, until we've measured it (`./jm bench`).
- "Impossible without Thor."
- That it captures all tacit knowledge, cuts training time, or reduces callbacks.
- The 70% figure as a measured fact.
- That it is safe for energized equipment. The demo cable is unpowered.
- That LiteRT runs on the Mac or does the listening, until we've seen it there. Fine to say: "Gemma 4 E2B on LiteRT-LM answers from the card offline" (verified on a Linux CPU).
