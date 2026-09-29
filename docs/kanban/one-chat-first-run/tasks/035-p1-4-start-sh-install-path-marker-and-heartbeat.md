---
id: 35
title: P1.4 start.sh install-path marker and heartbeat value
status: review
priority: medium
created: 2026-09-27T21:54:50.731375903Z
updated: 2026-09-29T04:29:32.05308435Z
tags:
    - phase-1
    - backend
    - telemetry
parent: 16
depends_on:
    - 33
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.052701502Z
class: standard
---

A distinct install-path value in the beacon closed set, derived from a marker start.sh leaves; update tests and README field table.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): start.sh and start.ps1 write an install-origin marker (value 'start') in the data home; beacon.py reports it as the install-path value. Tests: test_start_sh.py, test_start_ps1.py, test_beacon.py. Docs: cli.md, metrics.md.
