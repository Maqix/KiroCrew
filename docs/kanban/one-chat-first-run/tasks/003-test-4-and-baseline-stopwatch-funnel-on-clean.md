---
id: 3
title: 'Test 4 and baseline: stopwatch funnel on clean machines'
status: todo
priority: high
created: 2026-09-27T20:50:52.315163063Z
updated: 2026-09-27T22:00:11.675083283Z
tags:
    - phase-0
    - measurement
    - test
class: standard
---

Clean Ubuntu 24.04, macOS 15 and Windows 11, 5 people new to Kiro: split machine time from human time from curl ... cli.sh to first agent reply and first value. Pivot: harness sign-in alone over 2 min at p50.

[[2026-09-27]] Sun 21:55
Split: machine-time baseline can be automated in a clean container; the human stopwatch part needs 5 newcomers.

[[2026-09-27]] Sun 22:00
Machine-time baseline 2026-09-27, clean ubuntu:24.04 container, host network, --system-python (managed-Python uv download from GitHub releases returned 403 on this network): cli.sh install 36 s to 'Installed kirocrew 0.7.1'; gateway answers HTTP 5 s after 'kirocrew gateway'. Commands the user must still type after the one-liner: PATH fix, kirocrew gateway, kirocrew token (or find the URL), plus kiro-cli install and sign-in. Human-time stopwatch still needs participants.
