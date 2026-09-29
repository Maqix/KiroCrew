---
id: 38
title: P2.1 Pending-action card store and commit/decline endpoints (S1)
status: review
priority: critical
created: 2026-09-27T21:54:50.810828961Z
updated: 2026-09-29T04:29:32.136396551Z
tags:
    - phase-2
    - backend
    - security
parent: 17
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.136203944Z
class: standard
---

Server-side pending actions (id, owner, payload, payload hash); REST commit/decline bound to owner+id+hash; handler registry per card kind; tests for S1.

[[2026-09-27]] Sun 22:54
Backend implemented and unit-tested (test_setup_cards.py, test_setup_flow.py, test_setup_cards_api.py, test_setup_mcp_tools.py, test_secret_capture.py); waiting on the frontend card renderer and an end-to-end demo.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): src/kiro_crew/setup_cards.py (durable store, payload hash, statuses) and dashboard/handlers/setup_cards.py (GET /api/setup/cards/{id}, POST …/decide bound to owner + card id + hash, SC1). Tests: test_setup_cards.py, test_setup_cards_api.py, test_setup_actions.py (every commit needs the shown hash).
