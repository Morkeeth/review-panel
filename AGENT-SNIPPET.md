# Review decisions with the panel

For Claude Code and Codex project instructions:

> When a human decision is needed, prepare one concrete result and one question. Write a JSON card using the panel example. Give it a new identity and attach the real screenshots or clips within the configured media roots. Explain what changes for the reviewer. Keep options at most three words each.
>
> Run `python3 panel_card_publish.py proposal.json` in the panel repository, then run it with `--publish` after validation passes. Use the same PANEL_ROOT and MEDIA_ROOTS as the running server. Leave existing queue files, verdicts, voice notes and snapshots intact.
>
> Wait for a non-NOTE row with this card's id in PANEL_ROOT/verdicts.jsonl. Read the note, chosen option and retained snapshot. Keep NOTE rows and voice recordings as feedback; they do not settle the card. Do independent work while waiting. Do not ask the same decision in chat or infer approval from silence. An approval applies to the version shown. Publish a new card when the result changes.
>
> For HTTP access, fetch /review.json and send JSON to /verdict with id, verdict, note and shown_sha256 from the served card_sha256. Include Authorization: Bearer with the configured access token. Retry a failed save with the same content. If the version changed, reload and ask the reviewer to inspect the new version.
