---
id: 94
title: PN.11 Sign in to AWS from the home card (no terminal)
status: review
priority: medium
created: 2026-09-28T14:39:22.817225847Z
updated: 2026-09-29T04:29:33.457570045Z
tags:
    - parallel-nest
    - backend
    - frontend
parent: 58
claimed_by: lead
claimed_at: 2026-09-29T04:29:33.457286347Z
class: standard
---

Result (board audit 2026-09-29, branch feat/one-chat-first-run): dashboard/setup_aws_signin.py: 'Sign in to AWS' runs aws login from the card (same machine), or shows aws login --remote; exit 253 → profile holds keys. Probed against the real AWS CLI 2.34.19. Tests: test_setup_aws_signin.py, SetupCardHomeAwsSignin.test.tsx.
