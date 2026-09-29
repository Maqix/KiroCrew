---
id: 105
title: D5 Re-record the demos for the home step and the size choice
status: review
priority: medium
created: 2026-09-28T23:31:11.447622231Z
updated: 2026-09-29T00:04:46.325874328Z
tags:
    - demo
claimed_by: onboarding-video
claimed_at: 2026-09-29T00:04:46.325872931Z
class: standard
---

The recorded demos predate the 'Where should your crew live?' step, the size options and the setup.sh one-liner. Recut the first-run demo (and, if useful, the explainer's steps 1–3) from a setup.sh --demo run. Same safety rules: isolated homes, MCP servers off, the home simulated.

[[2026-09-29]] Done: kiro-crew-first-run-demo.mp4 recut (3:24) from a setup.sh --demo run on the current tree (commit 87d7f5716 plus the working tree): the terminal asks nothing; privacy; the 'Where should your crew live?' step card with Lite / Starter / Standard and their prices, a size picked, Build my home (simulated); hello; import; preview and keep; main chat; 'what's going on?'; pasted token; move in (simulated). The previous cut is kept as -v2. The terminal masks the token, hostname, home path and the operator's MCP server names. The demo script's make build was skipped with its own build mark (removed after), because npm ci would wipe node_modules under other agents; the dashboard was rebuilt and copied to static/dist first. The explainer was left as is (steps 1-3 need new narration and diagrams); README notes it. Temp homes removed. The PN.19 region picker did not show (the region was answered).
