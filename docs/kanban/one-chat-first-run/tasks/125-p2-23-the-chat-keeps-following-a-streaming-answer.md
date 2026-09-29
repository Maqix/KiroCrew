---
id: 125
title: P2.23 The chat keeps following a streaming answer while the setup tray is open
status: todo
priority: medium
created: 2026-09-29T19:34:41.153694568Z
updated: 2026-09-29T19:34:41.153694568Z
tags:
    - phase-2
    - frontend
    - ux
parent: 121
class: standard
---

Seen twice by the D8 recording driver (not in the published take): with the pending setup card tray open, the 'What's going on?' answer streamed in but the transcript stopped following it; the 'Scroll to bottom' arrow appeared and the answer stayed below the fold. Likely the tray's height change (open, cap, fold) shifts the scroller so the at-bottom check reads false and autoscroll stops. Expected: a user who was at the bottom stays at the bottom while a turn streams, whatever the tray does; only a real upward gesture detaches. Check PendingSetupCards.tsx / setupCardTray.ts against the ChatPane stick-to-bottom logic; add a test where the tray opens or folds mid-stream.
