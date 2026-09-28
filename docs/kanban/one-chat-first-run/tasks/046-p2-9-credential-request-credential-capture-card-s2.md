---
id: 46
title: P2.9 credential_request + credential capture card (S2)
status: review
priority: critical
created: 2026-09-27T21:54:51.056926654Z
updated: 2026-09-27T22:54:56.528328875Z
tags:
    - phase-2
    - backend
    - frontend
    - security
parent: 17
depends_on:
    - 38
claimed_by: claude
claimed_at: 2026-09-27T22:54:56.527716674Z
class: standard
---

Secret typed into a card goes straight to the vault; model gets secret://NAME only; S2 test with a sentinel secret.

[[2026-09-27]] Sun 22:54
Backend implemented and unit-tested (test_setup_cards.py, test_setup_flow.py, test_setup_cards_api.py, test_setup_mcp_tools.py, test_secret_capture.py); waiting on the frontend card renderer and an end-to-end demo.
