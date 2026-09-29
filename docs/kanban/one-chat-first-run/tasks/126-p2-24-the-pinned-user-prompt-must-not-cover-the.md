---
id: 126
title: P2.24 The pinned user prompt must not cover the row under it
status: todo
priority: low
created: 2026-09-29T20:06:42.133495106Z
updated: 2026-09-29T20:06:42.133495106Z
tags:
    - phase-2
    - frontend
    - ux
class: standard
---

Seen in the D8 recording at 9c07dfd9a: the pinned user prompt at the top of the chat covers the transcript row directly under it, e.g. the 'moved 1 pasted secret' notice at the vault step, so that notice is unreadable until the user scrolls. Expected: the pinned prompt reserves its own height (or the row under it is offset) so no row is hidden beneath it.
