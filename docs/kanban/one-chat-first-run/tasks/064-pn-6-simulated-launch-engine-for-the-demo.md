---
id: 64
title: PN.6 Simulated launch engine for the demo
status: review
priority: high
created: 2026-09-27T22:54:57.434367372Z
updated: 2026-09-27T23:35:34.967051684Z
tags:
    - parallel-nest
    - demo
parent: 58
depends_on:
    - 60
claimed_by: claude
claimed_at: 2026-09-27T23:35:34.966474521Z
class: standard
---

A LaunchEngine double that walks the real progress steps without AWS, labelled as simulated on screen.

[[2026-09-27]] Sun 23:35
Implemented: start_launch_job shared with /api/cloud/launch; home card (kind home) with build then move-in; watcher mirrors job steps; SimulatedLaunchEngine behind KIROCREW_CLOUD_SIMULATE=1. Tests: TestHomeInTheBackground.
