---
id: 121
title: P2.22 Pending setup cards must not crowd the chat
status: review
priority: high
created: 2026-09-29T15:17:57.520774382Z
updated: 2026-09-29T16:38:52.248574183Z
tags:
    - phase-2
    - frontend
    - ux
class: standard
---

Teammate feedback 2026-09-29: the sticky card tray above the composer took most of the chat; while the home card was pinned, the agent's question about Hermes had a sliver of space. Keep the agent's newest message readable at all times: the tray gets a height cap and collapses to a one-line bar ('Where should your crew live? · needs you · Show') when the transcript has newer content or the user scrolls up to read; the card also stays inline in the transcript, and the bar scrolls to it or expands it.

[[2026-09-29]] Tue 16:38
Done, not committed. The tray (PendingSetupCards.tsx) is capped at a third of its pane (measured from data-setup-tray-pane, 33dvh fallback) and scrolls inside. It folds to a one-line bar ('<title> · needs you · N cards · Show') when the transcript has anything newer than the card (setupCardTray.ts setupCardAtTail skips tool, reasoning and empty rows) or on an upward scroll gesture (wheel, touch, keys on the scroller, not geometry, so folding can't loop). A folded card stays mounted but inert, so half-filled input survives, and the tray never folds while focus is inside a card. Show/Hide carries aria-expanded and aria-controls. The bar's title (ChatPage only) opens the turn fold, scrolls to the card's row and highlights it (SetupCardRow.tsx). Browser check: /tmp/kc-demo/tray-check.mjs against the built SPA with a pending home card plus a long Hermes message, desktop and mobile; screenshots in /tmp/kc-demo/out-tray. Tests: PendingSetupCards.fold.test.tsx (12), transcriptRenderers row type updated; 108 ChatPage*/ChatPane*/tray suites green. New strings in components.setupCardTray, all catalogs, plural registered. Spec: first-run.md Pieces row. dist rebuilt and synced.
