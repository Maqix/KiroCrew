---
id: 40
title: P2.3 First-run session, state file (S6), privacy card first
status: review
priority: critical
created: 2026-09-27T21:54:50.864611518Z
updated: 2026-09-27T22:54:56.376738752Z
tags:
    - phase-2
    - backend
    - frontend
parent: 17
depends_on:
    - 38
claimed_by: claude
claimed_at: 2026-09-27T22:54:56.376469651Z
class: standard
---

Create the pinned first-run session on first start; state file under the data home that gates nothing; deterministic privacy card before the first model turn; chapters do not auto-open for first-run installs.

[[2026-09-27]] Sun 22:54
Backend implemented and unit-tested (test_setup_cards.py, test_setup_flow.py, test_setup_cards_api.py, test_setup_mcp_tools.py, test_secret_capture.py); waiting on the frontend card renderer and an end-to-end demo.
