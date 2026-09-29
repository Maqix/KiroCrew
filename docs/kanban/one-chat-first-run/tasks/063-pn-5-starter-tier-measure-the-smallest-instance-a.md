---
id: 63
title: 'PN.5 Starter tier: measure the smallest instance a home runs on'
status: review
priority: medium
created: 2026-09-27T22:54:57.40495797Z
updated: 2026-09-28T23:30:43.320046027Z
tags:
    - parallel-nest
    - measurement
parent: 58
claimed_by: lead
claimed_at: 2026-09-28T23:30:43.319877712Z
class: standard
---

Measured locally: idle gateway + one open chat ≈ 0.6 GB (gateway ≈ 0.2 GB, each kiro-cli chat ≈ 0.25 GB). The on-box dashboard build (6 GB heap) is what rules out a 2 GB instance; installing the prebuilt wheel instead of building from source would allow t4g.small. Recorded under RFC Q8.

Superseded by the measured sizing (PN.16, PN.18): idle gateway 1.3 GB, about 0.4 GB per open chat, and a 2.6 GB on-box dashboard build (now skipped via PN.17). The sizes offered are Lite 2 GB, Economy 4 GB, Small/Starter 8 GB and Standard 16 GB.
