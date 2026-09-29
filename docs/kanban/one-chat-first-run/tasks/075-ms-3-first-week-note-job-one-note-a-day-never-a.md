---
id: 75
title: MS.3 First-week note job (one note a day, never a card)
status: review
priority: medium
created: 2026-09-28T00:24:27.687160475Z
updated: 2026-09-29T04:29:33.172917575Z
started: 2026-09-28T03:27:06.687770571Z
tags:
    - muse
    - phase-2d
parent: 72
claimed_by: lead
claimed_at: 2026-09-29T04:29:33.172711167Z
class: standard
---

Gateway-owned system job, at most one note a day for 7 days in the main chat; picks the most useful unset thing; stops on request or after two unanswered notes; deletes itself. SC5, SC8. RFC §5.8, Q12.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): dashboard/first_week.py: at most one fixed notice a day for a week, never a card, stops on request. Tests: test_first_week.py.
