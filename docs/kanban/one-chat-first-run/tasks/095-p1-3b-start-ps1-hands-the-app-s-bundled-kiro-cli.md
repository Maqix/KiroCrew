---
id: 95
title: P1.3b start.ps1 hands the app's bundled kiro-cli to kirocrew start (Q14)
status: review
priority: medium
created: 2026-09-28T16:23:08.545168927Z
updated: 2026-09-28T16:23:08.545168927Z
tags:
    - installer
    - windows
claimed_by: lead
claimed_at: 2026-09-28T16:23:08.545193783Z
class: standard
---

RFC Q14 prototype answer, 2026-09-28: start.ps1 ran the desktop app's bundled kirocrew outside the app, so a machine with only the bundled kiro-cli was told the harness was missing. Get-KsBundledKiroEnv derives <resources>\backend-dist\kiro-cli from the bundled CLI's own path (never searched), probes kiro-cli.exe --version (10 s, no window, System.Diagnostics.Process so Start-Process stays the one signature-checked download), and sets KIROCREW_BUNDLED_KIRO_DIR + KIRO_NO_AUTO_UPDATE=1 for the child only, restored after (irm | iex runs in the user's session). KIROCREW_KIRO_BIN still wins. Tests: test_start_ps1.py (42 passed under a real pwsh 7.4.6 on Linux, including a positive run, not-bundled, does-not-run-here, and env restored). Docs: cli.md, windows-install.md, RFC Q14. Not verified on real Windows.
