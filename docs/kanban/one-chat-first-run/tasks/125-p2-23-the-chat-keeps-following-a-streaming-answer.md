---
id: 125
title: P2.23 The chat keeps following a streaming answer while the setup tray is open
status: review
priority: medium
created: 2026-09-29T19:34:41.153694568Z
updated: 2026-09-29T21:02:53.604534869Z
tags:
    - phase-2
    - frontend
    - ux
parent: 121
class: standard
---

Seen twice by the D8 recording driver (not in the published take): with the pending setup card tray open, the 'What's going on?' answer streamed in but the transcript stopped following it; the 'Scroll to bottom' arrow appeared and the answer stayed below the fold. Likely the tray's height change (open, cap, fold) shifts the scroller so the at-bottom check reads false and autoscroll stops. Expected: a user who was at the bottom stays at the bottom while a turn streams, whatever the tray does; only a real upward gesture detaches. Check PendingSetupCards.tsx / setupCardTray.ts against the ChatPane stick-to-bottom logic; add a test where the tray opens or folds mid-stream.

[[2026-09-29]] Tue 21:02
Done, not committed. Root cause: chrome BELOW the transcript (the setup tray folding to its bar) GROWS the scroller; the engine lowers max scrollTop and clamps a flush follower below our last write, and the clamp's scroll event dispatches a frame later, so streaming output that lands first reads as a user scroll-up and evaluateAutoPin released follow. Fix: FollowController.evaluateAutoPin now takes viewportGrowth (mirror of viewportShrink) and upwardInputWithinSettle; a follower sitting exactly where our own growth clamped them is treated as resting on our write (re-pin), while a real drag past the clamp or an upward input still releases. useVirtualChat passes -viewportShrink and the upward-input stamp on both the post-paint and pre-paint pin paths. The 'genuine upward input releases' rules are unchanged (an upward stamp voids the allowance). Deterministic repro: test/useVirtualChat.trayFold.test.tsx (tray folds/opens mid-stream, follow survives; a real scroll-up still detaches) plus 4 unit cases in FollowController.test.ts; both new files FAIL on HEAD f3e88fd85. Gates: tsc + eslint clean, 164-file virtualizer/chat sweep green (1861 pass). No prose spec owns the follow path; FollowController.ts carries the doc comments and I updated them. e2e-gate.md requires the webkit-mobile lane (chat-visibility-replace.webkit.spec.ts) by hand before PR review for a FollowController.ts change — I could not run it here (needs the mcr.microsoft.com/playwright image; not pulled), so the PR author must run it. Browser: exercised the follow path in the isolated demo gateway but the intermittent clamp is not reliably reproducible live; the deterministic test is the proof.
