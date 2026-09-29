---
id: 34
title: P1.3 start.ps1 counterpart
status: review
priority: medium
created: 2026-09-27T21:54:50.702542279Z
updated: 2026-09-29T04:29:32.015137788Z
tags:
    - phase-1
    - installer
    - windows
parent: 16
depends_on:
    - 33
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.014609731Z
class: standard
---

PowerShell counterpart beside install.ps1.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): start.ps1 (repo root): installs the signed desktop installer silently, then runs its bundled kirocrew start, handing it the app's bundled kiro-cli (Q14, P1.3b #95). Tests: test/test_start_ps1.py (42, incl. behaviour under a real pwsh 7 on Linux). Not run on real Windows (QA.2).
