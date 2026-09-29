---
id: 40
title: P2.3 First-run session, state file (S6), privacy card first
status: review
priority: critical
created: 2026-09-27T21:54:50.864611518Z
updated: 2026-09-29T04:29:32.227366497Z
tags:
    - phase-2
    - backend
    - frontend
parent: 17
depends_on:
    - 38
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.226898146Z
class: standard
---

Create the pinned first-run session on first start; state file under the data home that gates nothing; deterministic privacy card before the first model turn; chapters do not auto-open for first-run installs.

[[2026-09-27]] Sun 22:54
Backend implemented and unit-tested (test_setup_cards.py, test_setup_flow.py, test_setup_cards_api.py, test_setup_mcp_tools.py, test_secret_capture.py); waiting on the frontend card renderer and an end-to-end demo.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): first_run.py (state file, SC6: gates nothing, update_state serializes writes), setup_flow.ensure_first_run_session, the privacy card before any model turn. Tests: test_setup_flow.py, test_first_run_state.py, App.firstRunGating.test.tsx, useTheme.firstRunSlot.test.tsx.
