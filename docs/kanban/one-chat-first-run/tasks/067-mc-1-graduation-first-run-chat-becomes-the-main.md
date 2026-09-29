---
id: 67
title: 'MC.1 Graduation: first-run chat becomes the main chat'
status: review
priority: high
created: 2026-09-28T00:06:11.395888455Z
updated: 2026-09-29T04:29:32.915118715Z
tags:
    - main-chat
    - backend
parent: 66
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.91488989Z
class: standard
---

On the first kept job: rename after the agent, keep pinned, record main, main_chat notice. setup_flow.graduate; tests TestMainChat.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): setup_flow.graduate: keeping the first job renames the chat after the agent, pins it, records it as the main chat and posts the notice. Tests: test_setup_flow.py (TestMainChat), shown in the demos.
