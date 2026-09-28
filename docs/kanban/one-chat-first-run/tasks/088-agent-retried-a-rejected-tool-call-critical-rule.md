---
id: 88
title: Agent retried a rejected tool call (critical rule added)
status: review
priority: medium
created: 2026-09-28T03:27:06.717944528Z
updated: 2026-09-28T03:27:06.717944528Z
tags:
    - main-chat
    - context
parent: 66
class: standard
---

Demo finding: after a user rejected a tool call the agent re-issued it every ~35 s. [CRITICAL RULES] now says a rejection is a decision: do not call that tool again for the same purpose in the turn; say what it was for and carry on or ask. Golden extents updated (test_memory_v1_golden).
