---
id: 39
title: P2.2 Card renderer in the chat (frontend, i18n)
status: review
priority: critical
created: 2026-09-27T21:54:50.837333211Z
updated: 2026-09-29T04:29:32.179948387Z
tags:
    - phase-2
    - frontend
parent: 17
depends_on:
    - 38
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.179308725Z
class: standard
---

Render card kinds inline in the transcript from the stored payload; high-stakes styling; classic-setup escape; persisted across reload; i18n strings.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): website/src/components/setup/ (SetupCard.tsx, SetupCardBodies.tsx, setupCardCopy.tsx, setupCardRegistry.tsx, PendingSetupCards.tsx); copy in all 12 catalogs. Tests: SetupCard*.test.tsx, PendingSetupCards.test.tsx, useWebSocket.setupCardUpdate.test.ts; i18n:check passes.
