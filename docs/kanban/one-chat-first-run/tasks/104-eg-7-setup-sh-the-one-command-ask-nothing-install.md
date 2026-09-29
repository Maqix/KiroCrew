---
id: 104
title: 'EG.7 setup.sh: the one-command, ask-nothing install from source'
status: review
priority: high
created: 2026-09-28T23:31:11.409466074Z
updated: 2026-09-28T23:31:11.409466074Z
tags:
    - installer
class: standard
---

Done and pushed 2026-09-28 (9747987c5..d004dd9c1). curl …/setup.sh | bash fetches the branch into ~/.local/share/kirocrew/source (it never takes the current folder for a checkout), builds it with spinners, skips the optional tools, puts kirocrew on PATH, and ends in kirocrew start: a fresh crew opens the first-run chat, an existing one its main chat. --demo runs scripts/demo-first-run.sh: temporary folders, a simulated home, a sample agent, MCP servers off. Fixed on the way: pip prefers wheels (numpy on AL2), macOS libs are re-signed, a stale plain gateway on this crew's port is restarted, and an installed service is never stopped. That last one: a test run stopped this host's own service, which was restarted within a minute. Verified end to end on a clean HOME, real and demo; not yet on macOS.
