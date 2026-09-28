---
id: 97
title: PN.13 Create an AWS account from the home card (signup path)
status: review
priority: medium
created: 2026-09-28T17:06:13.826572784Z
updated: 2026-09-28T17:24:39.865018069Z
tags:
    - parallel-nest
    - backend
    - frontend
parent: 58
claimed_by: aws-signin-card
claimed_at: 2026-09-28T17:24:39.864856326Z
class: standard
---

A smoother AWS signup for someone with no AWS account, from the home card's not-signed-in view. The card never embeds, frames or proxies AWS pages (ARCC BSC10).

Result (in review, uncommitted):
- `cloud/local_signin.py`: adds `signup_url(builder_id)` (the `?request_type=builderId` page, else `SIGNUP_URL`) and `kiro_signs_in_with_builder_id()`. The latter runs one bounded `discover_local_identity(timeout=10)`, and only account_type == BuilderId counts.
- `setup_flow._home_payload`: only when the home is not simulated and AWS is not signed in, it adds `signup_url`, `signup_builder_id` and `aws_cli_installed`. A signed-in card is unchanged and runs no whoami.
- Home card: a "Create an AWS account" link in a new tab, through `safeConsentUrl`; Builder ID copy when it applies. The link leads to a local, untimed "finish creating your account" state with "I've created it — sign in" (runs `aws_signin`) and Back. A missing AWS CLI adds one line linking the official install page.
- crew-setup SKILL.md: with no AWS account, propose `kind: home` anyway; say creating the account is free and the home costs the stated estimate. The Hello doesn't offer a home without AWS; that waits on PN.12 (new accounts are region-locked by country).
- Docs: first-run.md "Signing in to AWS", RFC §6.8 rule 2.
- Tests: test/test_home_signup.py, website SetupCardHomeSignup.test.tsx, and locale keys in all 12 catalogs.

Unverified: that `request_type=builderId` lands on a Builder ID signup page (the URL came from the task).
