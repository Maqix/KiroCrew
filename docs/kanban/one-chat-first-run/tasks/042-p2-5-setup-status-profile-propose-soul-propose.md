---
id: 42
title: P2.5 setup_status, profile_propose, soul_propose + SOUL/USER context block
status: review
priority: high
created: 2026-09-27T21:54:50.922685795Z
updated: 2026-09-29T04:29:32.324451157Z
tags:
    - phase-2
    - backend
parent: 17
depends_on:
    - 38
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.324075137Z
class: standard
---

MCP tools and CLI twins; SOUL.md/USER.md under the data home with a size cap, injected via context blocks.

[[2026-09-27]] Sun 22:54
Backend implemented and unit-tested (test_setup_cards.py, test_setup_flow.py, test_setup_cards_api.py, test_setup_mcp_tools.py, test_secret_capture.py); waiting on the frontend card renderer and an end-to-end demo.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): Shipped as one directive tool setup_card plus setup_status (mcp_tools/setup.py; schema generated from setup_actions/), not one tool per kind (RFC §6.4). SOUL.md/USER.md reach the model as [AGENT PERSONA] / [USER NOTES] (context_blocks.py), capped at 3000 chars. Tests: test_setup_mcp_tools.py, test_setup_actions.py, test_setup_flow.py.
