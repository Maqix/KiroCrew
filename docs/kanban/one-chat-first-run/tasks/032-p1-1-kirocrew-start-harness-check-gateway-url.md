---
id: 32
title: 'P1.1 kirocrew start: harness check, gateway, URL, browser or QR'
status: review
priority: high
created: 2026-09-27T21:54:50.6517967Z
updated: 2026-09-27T23:13:12.517094955Z
tags:
    - phase-1
    - backend
parent: 16
claimed_by: installer-agent
claimed_at: 2026-09-27T23:13:12.516913927Z
class: standard
---

New CLI command that checks harness readiness, starts or reuses the gateway, waits for health, mints the one-time sign-in URL, opens the browser (or prints URL + terminal QR on a headless host) on the first-run session.

[[2026-09-27]] Sun 23:13
Implemented: src/kiro_crew/cli_start.py, start.sh, qr.render_qr_terminal, publish-installer.yml steps, docs. test_cli_start.py (29), test_start_sh.py (14); combined run 465 passed.
