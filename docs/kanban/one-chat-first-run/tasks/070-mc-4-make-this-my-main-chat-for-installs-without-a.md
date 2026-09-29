---
id: 70
title: MC.4 'Make this my main chat' for installs without a first run
status: review
priority: medium
created: 2026-09-28T00:06:11.488447154Z
updated: 2026-09-29T04:29:33.02261414Z
tags:
    - main-chat
parent: 66
claimed_by: lead
claimed_at: 2026-09-29T04:29:33.022425198Z
class: standard
---

A deliberate affordance (command or menu) that records any chat as the main chat.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): 'Make this my main chat' (POST /api/setup/main-chat, MainChatMenuItems.tsx). Tests: MainChatMenu.test.tsx, test_setup_flow.py.
