---
id: 64
title: PN.6 Simulated launch engine for the demo
status: review
priority: high
created: 2026-09-27T22:54:57.434367372Z
updated: 2026-09-29T04:29:32.881177919Z
tags:
    - parallel-nest
    - demo
parent: 58
depends_on:
    - 60
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.880753947Z
class: standard
---

A LaunchEngine double that walks the real progress steps without AWS, labelled as simulated on screen.

[[2026-09-27]] Sun 23:35
Implemented: start_launch_job shared with /api/cloud/launch; home card (kind home) with build then move-in; watcher mirrors job steps; SimulatedLaunchEngine behind KIROCREW_CLOUD_SIMULATE=1. Tests: TestHomeInTheBackground.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): cloud/simulated_engine.py (KIROCREW_CLOUD_SIMULATE=1): the same progress steps without AWS, labelled 'Simulated' on the card; used by setup.sh --demo and every demo. Tests: test_setup_flow.py, test_setup_move_in.py.
