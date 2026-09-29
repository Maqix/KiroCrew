---
id: 68
title: MC.2 [CREW OVERVIEW] in every main-chat turn
status: review
priority: high
created: 2026-09-28T00:06:11.433592849Z
updated: 2026-09-29T04:29:32.950517311Z
tags:
    - main-chat
    - backend
parent: 66
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.950331661Z
class: standard
---

Other chats and status, open setup cards, jobs due, home state; titles flattened; capped. setup_flow.crew_overview + chat_runner prefix.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): context_blocks.py [CREW OVERVIEW] in every main-chat turn (other chats, open cards, next jobs, the home), bounded. Tests: the context-block tests and test_setup_flow.py.
