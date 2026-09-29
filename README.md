# Evidence for PR #14906 — Crewmate DM follows new messages

Orphan evidence branch (not part of the PR diff). Captured with Playwright on the
real Crewmates page (`website/capture/members-page.tsx` mounted by the Vite dev
server), 1280x820, Chromium.

- `01-idle-reply-follows-{dark,light}.png` — reader at the end, wheeled there (no
  movement); a complete reply lands with the turn over; transcript follows, no pill.
- `02-streaming-follows-dark.png` — a live reply streams and its working footer
  mounts; transcript at the end.
- `03-reading-kept-dark.png` — reader scrolled up; a reply lands; reader kept, pill offered.
- `04-return-follows-dark.png` — reader returned to the end by hand; next reply follows.
- `05-send-follows-dark.png` — reader scrolled up and sent; lands on their bubble.
- `before/` — the same harness on `origin/main` (7610731029): frame 02 stops 51px
  short of the footer; frame 05 stays mid-history with the pill.
- `dm-follow-sequence.{gif,mp4}` — the whole dark sequence in one recording.
- `harness/` — the capture script and the capture-page patch (`?idle=1`,
  `reply`/`chunk`/`done` frames) used to produce the frames; kept here so the
  run is reproducible without landing a one-off script in the repository.
