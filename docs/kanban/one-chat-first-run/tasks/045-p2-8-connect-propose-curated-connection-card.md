---
id: 45
title: P2.8 connect_propose (curated connection card)
status: review
priority: high
created: 2026-09-27T21:54:51.01272565Z
updated: 2026-09-29T04:29:32.433594898Z
tags:
    - phase-2
    - backend
    - frontend
parent: 17
depends_on:
    - 38
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.432965271Z
class: standard
---

Connect card driving the connections mint consent flow; capability reveal on completion.

[[2026-09-27]] Sun 22:54
Backend implemented and unit-tested (test_setup_cards.py, test_setup_flow.py, test_setup_cards_api.py, test_setup_mcp_tools.py, test_secret_capture.py); waiting on the frontend card renderer and an end-to-end demo.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): setup_actions/connect.py over the connections mint consent flow (curated registry slugs only). Tests: test_setup_flow.py, test_setup_cards.py (slug validation).
