---
id: 123
title: 'EG.8 No kiro-cli: open the web and guide the install there'
status: review
priority: high
created: 2026-09-29T15:20:10.793138711Z
updated: 2026-09-29T16:29:19.491468455Z
tags:
    - installer
    - phase-1
    - ux
class: standard
---

User question 2026-09-29: with kiro-cli missing, setup.sh warns early but builds for minutes anyway, and kirocrew start then exits 3 in the terminal with a link; no browser, and the user must re-run. Without deciding Q2 (auto-install), make it guided in the web: on an interactive run with the harness missing, kirocrew start still starts the gateway and opens the browser. The dashboard's existing KiroPrerequisiteGate shows the install and sign-in steps for this OS and checks again, and the first-run chat carries on once the harness is ready. --no-input keeps today's exit 3. setup.sh checks for kiro-cli first and says so in step 1. Q2 (the installer installing a pinned kiro-cli) stays open.

Done (uncommitted):
- kirocrew start: on a terminal (TTY, no --no-input) a missing kiro-cli prints one line ('kiro-cli isn't installed yet; the browser will walk you through it.') and carries on to the gateway and the browser. KAS missing only kiro-cli does the same. --no-input, no terminal, and a harness missing an adapter (claude, codex, pi) keep today's message and exit 3: the gate probes kiro-cli only.
- Gate: shows Kiro's install one-liner for the host platform as a copy block (install_command in the status payload, from kiro_prerequisite.install_command_for; empty for non-owners and unknown platforms) above the kiro.dev link, plus a no-browser sign-in hint. Existing poll (5 s) and Check again unchanged.
- Fix: _established_installation counted the first-run chat's own transcript and the session index, so any restart before setup dropped the gate and opened a chat that could not answer. Both are now discounted until a main chat is recorded; the setup marker still wins.
- setup.sh checks kiro-cli in step 1 and says the browser will guide the install; step 3 no longer repeats it.
- Tests: test_cli_start (+5), test_kiro_prerequisite (+6), KiroPrerequisiteGate.test.tsx (+3). 3 new keys in 12 catalogs; en-XA regenerated; dist rebuilt.
- Docs: cli.md (Start Command, prerequisite section), learn-cron-dashboard.md, install.md, windows-install.md, first-run.md, RFC 5.1 step 2 and Q2.
- Live run (Linux, isolated home, kiro-cli hidden): screenshots in temp-screenshots/eg8-no-kiro-cli/. Not live-verified: the final lift into the chat after a real sign-in, macOS, Windows.
