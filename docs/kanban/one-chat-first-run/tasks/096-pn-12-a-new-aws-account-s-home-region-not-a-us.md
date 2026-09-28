---
id: 96
title: PN.12 A new AWS account's home region, not a us-east-1 fallback
status: todo
priority: high
created: 2026-09-28T16:57:27.010360044Z
updated: 2026-09-28T16:57:27.010360044Z
tags:
    - parallel-nest
    - aws
    - home
class: standard
---

Finding from the AWS-signup research session (2026-09-28): accounts made through AWS's newest sign-up (Builder ID or social login) are pinned to one region chosen by country (e.g. us-east-2, eu-north-1, ap-southeast-2). When the AWS profile names no region, the home card falls back to setup_cards.HOME_DEFAULT_REGION (us-east-1), which fails for those accounts. Fix: resolve the account's own region before offering or building (how: pending the research's confirmation of a read-only way to learn it), and state it on the card. Related: a brand-new signup takes longer than the 300 s aws login wait (SIGNIN_WAIT_SECS); the card needs a 'creating your account' state with no timer. Blocked on the research report; do not change code yet.
