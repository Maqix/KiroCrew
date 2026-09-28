---
id: 60
title: PN.2 Background launch job observable from the gateway
status: review
priority: high
created: 2026-09-27T22:54:57.321423112Z
updated: 2026-09-27T23:35:34.817631183Z
tags:
    - parallel-nest
    - backend
parent: 58
depends_on:
    - 59
claimed_by: claude
claimed_at: 2026-09-27T23:35:34.817053674Z
class: standard
---

Start the existing launch engine detached with its progress in a status file under the data home; the gateway reads it.

[[2026-09-27]] Sun 23:35
Implemented: start_launch_job shared with /api/cloud/launch; home card (kind home) with build then move-in; watcher mirrors job steps; SimulatedLaunchEngine behind KIROCREW_CLOUD_SIMULATE=1. Tests: TestHomeInTheBackground.
