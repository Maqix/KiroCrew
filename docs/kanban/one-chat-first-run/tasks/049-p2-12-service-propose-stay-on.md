---
id: 49
title: P2.12 service_propose (stay on)
status: review
priority: medium
created: 2026-09-27T21:54:51.144321319Z
updated: 2026-09-29T04:29:32.581240233Z
tags:
    - phase-2
    - backend
parent: 17
depends_on:
    - 38
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.580851876Z
class: standard
---

Service card wrapping kirocrew service install with the sudo hand-off stated.

[[2026-09-27]] Sun 22:54
Backend implemented and unit-tested (test_setup_cards.py, test_setup_flow.py, test_setup_cards_api.py, test_setup_mcp_tools.py, test_secret_capture.py); waiting on the frontend card renderer and an end-to-end demo.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): setup_actions/service.py: kirocrew service install from the card (sudo on Linux handed to a terminal). Tests: test_setup_flow.py.
