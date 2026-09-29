---
id: 36
title: P1.5 Desktop first run lands in the first-run session
status: review
priority: low
created: 2026-09-27T21:54:50.756482245Z
updated: 2026-09-29T04:29:32.095066369Z
tags:
    - phase-1
    - desktop
parent: 16
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.094883466Z
class: standard
---

Electron's first load goes to the first-run session when one exists.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): The desktop app's first run lands in the first-run session (website/electron main + first-run landing). Tests: website/electron/test/first-run-landing.test.js.
