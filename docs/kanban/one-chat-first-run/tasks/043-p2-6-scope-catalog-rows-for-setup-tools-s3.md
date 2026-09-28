---
id: 43
title: P2.6 SCOPE_CATALOG rows for setup tools (S3)
status: review
priority: high
created: 2026-09-27T21:54:50.953918655Z
updated: 2026-09-27T22:54:56.434028417Z
tags:
    - phase-2
    - backend
    - security
parent: 17
depends_on:
    - 42
claimed_by: claude
claimed_at: 2026-09-27T22:54:56.433706312Z
class: standard
---

Governance rows so a profile can forbid setup actions; S3 test that no setup tool writes keystone files or sandbox/approval mode.

[[2026-09-27]] Sun 22:54
Backend implemented and unit-tested (test_setup_cards.py, test_setup_flow.py, test_setup_cards_api.py, test_setup_mcp_tools.py, test_secret_capture.py); waiting on the frontend card renderer and an end-to-end demo.
