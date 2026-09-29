---
id: 126
title: P2.24 The pinned user prompt must not cover the row under it
status: review
priority: low
created: 2026-09-29T20:06:42.133495106Z
updated: 2026-09-29T21:02:53.63836402Z
tags:
    - phase-2
    - frontend
    - ux
class: standard
---

Seen in the D8 recording at 9c07dfd9a: the pinned user prompt at the top of the chat covers the transcript row directly under it, e.g. the 'moved 1 pasted secret' notice at the vault step, so that notice is unreadable until the user scrolls. Expected: the pinned prompt reserves its own height (or the row under it is offset) so no row is hidden beneath it.

[[2026-09-29]] Tue 21:02
Done, not committed. The captured-secret notice ('moved 1 pasted secret') is written by the gateway JUST ABOVE the prompt it describes, so it is that prompt's lead-in, not the tail of the turn before. Two changes: (1) groupDisplayItems peels a run of prompt-lead-in notices (isPromptLeadInNotice: assistant role + kind secret_captured, in lib/systemNotice.ts) off the front of the previous turn and stands each on its own row, so it is never folded into 'Worked through N steps'; (2) utils/pinnedPrompt.pushRowIdx makes the incoming prompt's lead-in note push the pinned banner out exactly as the prompt does, so the banner is dropped before it can park over the note. usePinnedPrompt measures the push from that row. Tests: groupDisplayItems.test.ts (note stands alone above the prompt; other notices and a trailing note stay put; not peeled before a machine opener), pinnedPrompt.test.ts (pushRowIdx), usePinnedPrompt.test.tsx (pushes by the note's top, drops before the prompt reaches the fold, control with no lead-in); all fail on HEAD. Gates green (same sweep as #125). Spec: docs/feature-map/README.md pinned-prompt-banner row updated. Browser: the notice renders fully readable in the built product (isolated stub of the real dist, /tmp/p22-pin-stub/pinned-adjacent.png); the exact push math is pinned by the hook test.
