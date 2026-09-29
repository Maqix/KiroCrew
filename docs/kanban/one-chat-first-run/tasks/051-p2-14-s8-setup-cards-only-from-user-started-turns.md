---
id: 51
title: 'P2.14 S8: setup cards only from user-started turns outside the first run'
status: review
priority: high
created: 2026-09-27T21:54:51.200313756Z
updated: 2026-09-29T04:29:32.653376084Z
tags:
    - phase-2
    - backend
    - security
parent: 17
depends_on:
    - 38
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.653011056Z
class: standard
---

Refuse setup proposals in cron/watch/injected/subagent turns outside the first-run session; test.

[[2026-09-27]] Sun 22:54
Backend implemented and unit-tested (test_setup_cards.py, test_setup_flow.py, test_setup_cards_api.py, test_setup_mcp_tools.py, test_secret_capture.py); waiting on the frontend card renderer and an end-to-end demo.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): SC8: setup_flow.propose refuses a card in a turn a person did not start (cron, watch, injected event, sub-agent). Tests: test_setup_flow.py (provenance cases), test_setup_actions.py.
