---
id: 63
title: 'PN.5 Starter tier: measure the smallest instance a home runs on'
status: review
priority: medium
created: 2026-09-27T22:54:57.40495797Z
updated: 2026-09-28T03:44:37.328519115Z
tags:
    - parallel-nest
    - measurement
parent: 58
claimed_by: claude
claimed_at: 2026-09-28T03:44:37.328519055Z
class: standard
---

Measured locally: idle gateway + one open chat ≈ 0.6 GB (gateway ≈ 0.2 GB, each kiro-cli chat ≈ 0.25 GB). The on-box dashboard build (6 GB heap) is what rules out a 2 GB instance; installing the prebuilt wheel instead of building from source would allow t4g.small. Recorded under RFC Q8.
