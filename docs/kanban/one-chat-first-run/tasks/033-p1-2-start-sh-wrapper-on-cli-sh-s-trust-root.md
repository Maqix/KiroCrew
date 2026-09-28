---
id: 33
title: P1.2 start.sh wrapper on cli.sh's trust root
status: review
priority: high
created: 2026-09-27T21:54:50.677909632Z
updated: 2026-09-27T23:13:12.544297267Z
tags:
    - phase-1
    - installer
parent: 16
depends_on:
    - 32
claimed_by: installer-agent
claimed_at: 2026-09-27T23:13:12.544036526Z
class: standard
---

Thin POSIX script: runs the signed cli.sh install from the same origin, then execs `kirocrew start`. Published beside cli.sh; tests.

[[2026-09-27]] Sun 23:13
Implemented: src/kiro_crew/cli_start.py, start.sh, qr.render_qr_terminal, publish-installer.yml steps, docs. test_cli_start.py (29), test_start_sh.py (14); combined run 465 passed.
