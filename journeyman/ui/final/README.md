# final

The Journeyman monitor. `/` and `/ui/` redirect here (`/ui/final/`).

Combines the three Echo-style drafts:

- echo-terminal: masthead, prompt line, typed-in spoken line, session.log, procedure.md, health footer.
- echo-eye: the live camera frame beside the same frame drawn as purple ASCII ("journeyman's eye").
- echo-post: Echo's ASCII planet (`ascii.js`, adapted from jparker2006/Echo), moving with `voice_state`.

Added here:

- A text box under session.log. It POSTs `{text}` to `/api/headset/utterance`, the same path as speech, for when the mic fails.
- "open on phone" shows a QR code for this page's own URL. `qr.js` is a small offline encoder (byte mode, level M, versions 1-10).
  Open the page by the Thor's LAN IP, not localhost, or phones can't reach it. `?qr` opens the QR on load.

No build step, no dependencies, no external requests. Fonts are bundled with their OFL licenses.

`shots/`: capture and teach at 1920x1080, phone at 390x844, and the QR dialog. They come from `tests/fake_gemma.py` and an
illustrated test frame, not the Brio or a live Gemma.
