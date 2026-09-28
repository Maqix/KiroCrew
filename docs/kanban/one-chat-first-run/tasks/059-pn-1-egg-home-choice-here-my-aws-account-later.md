---
id: 59
title: 'PN.1 Egg home choice: here / my AWS account / later'
status: review
priority: high
created: 2026-09-27T22:54:57.290317462Z
updated: 2026-09-27T23:35:34.758674427Z
tags:
    - parallel-nest
    - installer
parent: 58
claimed_by: claude
claimed_at: 2026-09-27T23:35:34.758506255Z
class: standard
---

kirocrew start asks once (skippable, --home flag). AWS: detect the AWS CLI and credentials; if none, run the AWS CLI's own sign-in (SSO device flow or aws login) or open the signup page; show the monthly estimate and ask for a yes before launching.

[[2026-09-27]] Sun 23:35
kirocrew start --home here|cloud|later: aws login (browser OAuth+PKCE), region from the profile, cost line; recorded in first-run state (presentation only).
