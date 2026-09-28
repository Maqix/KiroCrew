---
id: 51
title: 'P2.14 S8: setup cards only from user-started turns outside the first run'
status: review
priority: high
created: 2026-09-27T21:54:51.200313756Z
updated: 2026-09-27T22:54:56.748780739Z
tags:
    - phase-2
    - backend
    - security
parent: 17
depends_on:
    - 38
claimed_by: claude
claimed_at: 2026-09-27T22:54:56.748515145Z
class: standard
---

Refuse setup proposals in cron/watch/injected/subagent turns outside the first-run session; test.

[[2026-09-27]] Sun 22:54
Backend implemented and unit-tested (test_setup_cards.py, test_setup_flow.py, test_setup_cards_api.py, test_setup_mcp_tools.py, test_secret_capture.py); waiting on the frontend card renderer and an end-to-end demo.
