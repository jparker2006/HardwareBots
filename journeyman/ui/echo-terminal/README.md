# echo-terminal

Open `/ui/echo-terminal/` on the running Journeyman service (port 3000 by default).
No build step, package dependencies, or external runtime requests.

The page fetches `/api/state` initially and on SSE events from `/api/events`
(coalesced over 180 ms, with one request in flight). A 10-second fallback poll
also refreshes state. The latest camera image refreshes each second; camera
status comes from `POST /api/ops/status` every three seconds. Stale frames keep
an explicit age label. Read-only display: voice and CLI remain the controls.

Echo's exact palette is used, with warm `#f2aa86` for corrections and stale camera
warnings. Geist, JetBrains Mono, and italic Newsreader Latin WOFF2 files are
bundled with their OFL licenses. System fallback fonts cover other glyphs.
The shaded ASCII planet draws at 30 fps, changes its spin and pulse with voice
state, stops drawing while hidden or offscreen, and holds still with reduced
motion. Initial output appears immediately; subsequent spoken lines type in.

Desktop panes scroll independently. On phones the content flows vertically:
spoken output, camera, procedure, then transcript. Live events never move the
phone's viewport. The transcript follows new lines only while at its bottom.

## Verification

- `python3 -m unittest discover -s tests -t .`: 130 tests passed, no skips.
- Live capture → question → answer → teach → camera correction, using
  `tests.fake_gemma` and JPEGs rendered from the supplied camera illustration.
- Browser checks: SSE updates, real expert quotes, done/current step states,
  local fonts, and no horizontal overflow at 390 px.
- `shots/capture.png`: 1920 × 1080; `shots/teach.png`: 1920 × 1080;
  `shots/phone.png`: 390 × 844. The camera in these shots is explicitly an
  illustrated test feed. Physical Thor / Brio hardware was not exercised.
