# Extraction map

This repository is built from components of a panel in daily use. Code was rewritten or reduced for a general setup; no private history is included.

| Component | Reuse |
| --- | --- |
| `review-next.html` | Original page layout, responsive styles, full-screen shell and microphone guard; private navigation removed; system font fallback |
| `panel.js` | Extracted reel renderer, media carousel, lazy loading, resize handling, note and verdict writes, one-shot recording and image viewer; queue loading and navigation adapted for the smaller server |
| `review-presentation.js/css` | Shared prose helpers and phone presentation rules; content-specific branches removed |
| `mic-outbox.js` | Retained IndexedDB chunks, audio integrity checks and durable acknowledgement; completion adapted for audio-only notes |
| `panel_audio.py` | Durable original audio archive and capture identity checks; storage defaults changed to the configured panel directory |
| `panel_verdict_log.py` | Original descriptor lock, append, fsync, request identity and readback boundary |
| `pwa/` | Original offline shell, logo, icons and network-only service worker; manifest description generalized |
| `server.py` | Reduced HTTP adapter for the working card shape, media, verdict, snapshot, voice and token-auth routes |
| `panel_card_publish.py` / `card_gate.py` | Reduced publishing contract: one question, plain why, exact repeat and identity protection, supported attachments and atomic creation |

The full edit timeline and domain-specific review workflows are outside this fork. The retained card flow includes pictures, A/B clips, full-screen playback, choices, notes, audio and history. Demo content is invented. The bundled demo media license is in `examples/MEDIA-LICENSE.txt`.
