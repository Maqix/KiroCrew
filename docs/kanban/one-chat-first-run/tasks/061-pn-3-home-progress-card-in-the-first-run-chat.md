---
id: 61
title: PN.3 Home progress card in the first-run chat
status: review
priority: high
created: 2026-09-27T22:54:57.348285162Z
updated: 2026-09-27T23:35:34.873526038Z
tags:
    - parallel-nest
    - backend
    - frontend
parent: 58
depends_on:
    - 60
claimed_by: claude
claimed_at: 2026-09-27T23:35:34.872994433Z
class: standard
---

Gateway-created deterministic card (like privacy) showing launch steps from the status file; the kickoff facts tell the agent a home is being built.

[[2026-09-27]] Sun 23:35
Implemented: start_launch_job shared with /api/cloud/launch; home card (kind home) with build then move-in; watcher mirrors job steps; SimulatedLaunchEngine behind KIROCREW_CLOUD_SIMULATE=1. Tests: TestHomeInTheBackground.
