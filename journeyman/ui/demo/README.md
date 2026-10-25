# Journeyman demo screen

Open `/` or `/ui/demo/` with `./jm serve`. Everything loads locally, including
Geist, JetBrains Mono and Newsreader from `../final/fonts/`. The planet and its
ASCII renderer are preserved from `final/`.

Press `/` to open the voice fallback, Enter or Send to submit, and Escape to close.
The spoken line is displayed verbatim. Lime means asking, amber means correcting,
and light ink means instructing. Teach mode adds the current step's recorded reason.
The checklist shows at most six steps and follows the current step automatically.

## Verification

Drove the real server on port 3111 against `tests/fake_gemma.py`, with a fresh
`JM_DATA`, `JM_CAMERA=none`, `JM_EXPERT=Jake`, and short observation/silence gates.
Pushed `recordings/demo-frames/frames/04-label-ends.jpg` to `/api/frames`, then used
`JM_URL=http://localhost:3111 ./jm hear ...` for capture → question → answer →
I'm done → teach me → next (caught skip) → why. Repushed the frame before next
so the camera check had a recent frame.

Headless Chromium captures at 1440×900, reviewed visually:

- [Capture asking](shots/capture-asking.png)
- [Teach correcting](shots/teach-correcting.png)
- [Ready after capture](shots/idle.png)
- [Teach reason and completed step](shots/teach-reason.png)

Also checked 1920×1080, an eight-step procedure limited to six visible rows,
no viewport overflow at either desktop size, `/` focus, Escape dismissal, and
submitting an utterance with the fallback form.
