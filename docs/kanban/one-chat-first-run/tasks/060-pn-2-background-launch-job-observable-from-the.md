---
id: 60
title: PN.2 Background launch job observable from the gateway
status: review
priority: high
created: 2026-09-27T22:54:57.321423112Z
updated: 2026-09-29T04:29:32.845970082Z
tags:
    - parallel-nest
    - backend
parent: 58
depends_on:
    - 59
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.845534141Z
class: standard
---

Start the existing launch engine detached with its progress in a status file under the data home; the gateway reads it.

[[2026-09-27]] Sun 23:35
Implemented: start_launch_job shared with /api/cloud/launch; home card (kind home) with build then move-in; watcher mirrors job steps; SimulatedLaunchEngine behind KIROCREW_CLOUD_SIMULATE=1. Tests: TestHomeInTheBackground.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): cloud/launch_job.py and the home card watcher (setup_flow._watch_home) mirror the build's steps onto the card; the build runs in the gateway, and since PN.22 (#117) a restart re-attaches the watcher. Tests: test_cloud_launch_job.py, test_home_resume.py. Verified on real AWS (live-89, Lite run).
