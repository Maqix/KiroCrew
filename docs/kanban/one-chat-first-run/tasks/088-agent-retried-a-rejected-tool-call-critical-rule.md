---
id: 88
title: Agent retried a rejected tool call (critical rule added)
status: review
priority: medium
created: 2026-09-28T03:27:06.717944528Z
updated: 2026-09-29T04:29:33.351214211Z
tags:
    - main-chat
    - context
parent: 66
claimed_by: lead
claimed_at: 2026-09-29T04:29:33.351014793Z
class: standard
---

Demo finding: after a user rejected a tool call the agent re-issued it every ~35 s. [CRITICAL RULES] now says a rejection is a decision: do not call that tool again for the same purpose in the turn; say what it was for and carry on or ask. Golden extents updated (test_memory_v1_golden).

Result (board audit 2026-09-29, branch feat/one-chat-first-run): context.py critical rule: a rejected tool call is the user's decision, not an error to retry; the retried-reject seen in a recording was the demo driver's (fixed in the driver). Tests: test_memory_v1_golden extents updated.
