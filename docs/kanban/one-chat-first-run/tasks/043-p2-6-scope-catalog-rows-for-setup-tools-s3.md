---
id: 43
title: P2.6 SCOPE_CATALOG rows for setup tools (S3)
status: review
priority: high
created: 2026-09-27T21:54:50.953918655Z
updated: 2026-09-29T04:29:32.360256978Z
tags:
    - phase-2
    - backend
    - security
parent: 17
depends_on:
    - 42
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.359871681Z
class: standard
---

Governance rows so a profile can forbid setup actions; S3 test that no setup tool writes keystone files or sandbox/approval mode.

[[2026-09-27]] Sun 22:54
Backend implemented and unit-tested (test_setup_cards.py, test_setup_flow.py, test_setup_cards_api.py, test_setup_mcp_tools.py, test_secret_capture.py); waiting on the frontend card renderer and an end-to-end demo.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): One SCOPE_CATALOG row, capabilities.setup, with a kinds matcher; cron cards also pass capabilities.cron. Tests: test_setup_action_parity.py (every kind has its scope row), test_setup_actions.py (no commit past a governance denial).
