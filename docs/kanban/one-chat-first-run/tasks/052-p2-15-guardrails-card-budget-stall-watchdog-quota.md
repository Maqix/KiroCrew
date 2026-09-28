---
id: 52
title: 'P2.15 Guardrails: card budget, stall watchdog, quota card'
status: review
priority: medium
created: 2026-09-27T21:54:51.228138827Z
updated: 2026-09-28T03:04:37.010380246Z
tags:
    - phase-2
    - backend
parent: 17
depends_on:
    - 40
claimed_by: guardrails
claimed_at: 2026-09-28T03:04:37.010380165Z
class: standard
---

At most eight cards before the first kept job; deterministic stall and quota-exhausted cards.

[[2026-09-27]] Sun 22:54
Card budget done (8 before a kept job). Stall watchdog and quota card still to do.

[[2026-09-28]] Mon 03:04
Stall watchdog, kickoff notice and quota pause done (src/kiro_crew/dashboard/setup_guardrails.py; hook at the top of chat_runner._run_chat, depth 0). Stall: the session-health classifier on a private 90 s window (FIRST_RUN_STALL_SECS, sampled every 5 s) posts one setup_stalled/no_output notice per turn; approvals, questions, children, wait and recoveries are not stalls. Kickoff with no reply (or dispatch failure) posts setup_stalled/kickoff_failed with Try again (POST /api/setup/first-run/retry, owner-only; 409 kickoff_answered / turn_running / privacy_not_acked). Quota: a turn ending on the usage_limit error row posts one setup_quota notice per episode and propose() refuses cards until a turn lands a reply. Frontend: SetupGuardrailNotice (localized copy keyed on meta, classic setup button), 12 locales. Spec: first-run.md Guardrails. Tests: test/test_setup_guardrails.py (23), website/src/test/SetupGuardrailNotice.test.tsx (5).
