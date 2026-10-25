# echo-eye

Open `/ui/echo-eye/` on the existing Journeyman server. This folder is plain HTML, CSS and JavaScript; no build, package install, network font service or cloud service is required.

- State: `/api/state`; SSE `/api/events` triggers throttled state refreshes, with a five-second recovery poll.
- Camera: `/api/frame/latest.jpg`, fetched twice a second. The JPEG and ASCII view share one decoded image. The ASCII renderer samples luminance through Echo's ` .:;+*#%@` ramp and purple palette, with lime reserved for the brightest highlights and a cached glyph atlas. It preserves the camera aspect ratio. The voice planet animates at approximately 30 fps and responds to the reported voice state.
- Reduced motion: the ASCII camera holds a still frame; the voice planet changes only with state. Hidden tabs pause camera fetches and animation.
- Health: camera source and frame age come from `POST /api/ops/status`; latency uses actual notes. No latency estimate is invented.
- Session counters count steps created and reasons saved since the current session started, plus that session's mistakes. Teach progress uses `done_steps` and `teach_index` separately.
- Step reasons preserve `source` and confirmation status; keyframes open the original JPEG.

Typography and visual reference: [Echo](https://github.com/jparker2006/Echo), especially `app/globals.css`, `components/ascii-live.tsx`, and `lib/ascii.ts`. Locally vendored Latin WOFF2 subsets of Geist, JetBrains Mono and Newsreader include their OFL license texts in `fonts/`.

Verification: capture → question → saved answer → teach → camera correction → done/current transitions against `tests.fake_gemma`, using a JPEG rendered from the repository's camera mockup. Screenshots in `shots/` show the actual service state at 1920×1080 and 390×844. Thor hardware was not available in this sandbox. Python suite: 130 tests, 9 skipped, OK.
