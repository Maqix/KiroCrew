---
id: 48
title: 'P2.11 cron_propose: run once as a preview, then Keep it'
status: review
priority: critical
created: 2026-09-27T21:54:51.118222525Z
updated: 2026-09-29T04:29:32.544903227Z
tags:
    - phase-2
    - backend
    - frontend
parent: 17
depends_on:
    - 38
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.544330002Z
class: standard
---

Create the job disabled, run once now, show the output in the cron card; Keep it enables the schedule.

[[2026-09-27]] Sun 22:54
Backend implemented and unit-tested (test_setup_cards.py, test_setup_flow.py, test_setup_cards_api.py, test_setup_mcp_tools.py, test_secret_capture.py); waiting on the frontend card renderer and an end-to-end demo.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): setup_actions/cron.py and dashboard/setup_preview.py: the card runs the job once as a preview, shows the output and its approvals on the card (P2.17), then Keep it; hourly at most. Tests: test_setup_preview.py, test_setup_flow.py, SetupCard.test.tsx.
