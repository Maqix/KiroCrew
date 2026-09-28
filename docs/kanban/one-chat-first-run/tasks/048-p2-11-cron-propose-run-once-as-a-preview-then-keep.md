---
id: 48
title: 'P2.11 cron_propose: run once as a preview, then Keep it'
status: review
priority: critical
created: 2026-09-27T21:54:51.118222525Z
updated: 2026-09-27T22:54:56.629990104Z
tags:
    - phase-2
    - backend
    - frontend
parent: 17
depends_on:
    - 38
claimed_by: claude
claimed_at: 2026-09-27T22:54:56.629452374Z
class: standard
---

Create the job disabled, run once now, show the output in the cron card; Keep it enables the schedule.

[[2026-09-27]] Sun 22:54
Backend implemented and unit-tested (test_setup_cards.py, test_setup_flow.py, test_setup_cards_api.py, test_setup_mcp_tools.py, test_secret_capture.py); waiting on the frontend card renderer and an end-to-end demo.
