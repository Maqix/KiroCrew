---
id: 44
title: P2.7 import_scan / import_propose
status: review
priority: high
created: 2026-09-27T21:54:50.985024721Z
updated: 2026-09-29T04:29:32.395263208Z
tags:
    - phase-2
    - backend
parent: 17
depends_on:
    - 38
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.394913096Z
class: standard
---

Wrap detect_sources/preview_import/apply_import; preview card; adapted crons.

[[2026-09-27]] Sun 22:54
Backend implemented and unit-tested (test_setup_cards.py, test_setup_flow.py, test_setup_cards_api.py, test_setup_mcp_tools.py, test_secret_capture.py); waiting on the frontend card renderer and an end-to-end demo.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): setup_actions/import_.py wrapping the onboarding_import plan; imported crons arrive disabled and are listed in the result. Tests: test_setup_flow.py (import card and result), test_crew_setup_evals.py (Hermes fixture).
