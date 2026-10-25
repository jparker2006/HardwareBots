# Journeyman demo screen

Open `/` or `/ui/demo/` with `./jm serve`. Everything loads locally, including
Geist, JetBrains Mono and Newsreader from `../final/fonts/`. The planet and its
ASCII renderer are preserved from `final/`.

Press `/` to open the voice fallback, Enter or Send to submit, and Escape to close.
The spoken line is displayed verbatim. Lime means asking, amber means correcting,
and light ink means instructing. Teach mode adds the current step's recorded reason.
The right column is the session panel: a Recording/Teaching/Session saved badge,
a timer, the steps (at most six, kept scrolled to the current one), and big buttons
that post the same phrase you would say, so a click and a voice command take one path.

## Verification

Drove the real server on port 3111 against `tests/fake_gemma.py` (scripted to save
three steps), with a fresh `JM_DATA`, `JM_CAMERA=none`, `JM_EXPERT=Jake`,
`JM_PROCEDURE=usb-drive`, short observe/silence gates, and frames from
`recordings/demo-frames/frames/` pushed to `/api/frames`. Every state change below
was a click on the panel's own buttons in headless Chromium at 1710×980 (also
checked at 1440×900): Start capture → I'm done → Teach me → Next step (caught skip,
then advanced) → End. For Saving… the fake model's observe call was slowed by 8 s.

- [Idle, fresh](shots/1-idle.png)
- [Recording, no steps yet](shots/2-capture-empty.png)
- [Recording, three steps](shots/3-capture-steps.png)
- [Saving… while the last frames are reviewed](shots/4-saving.png)
- [Session saved, with duration](shots/5-saved.png)
- [Teaching step 2 with the expert's reason](shots/6-teach-step2.png)
- [Session saved after teaching](shots/7-after-teach.png)

Also checked a seven-step capture with wrapping titles at both sizes: the newest
step stays visible above the buttons, and nothing overflows the viewport.
