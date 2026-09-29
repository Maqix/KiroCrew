---
id: 77
title: MS.5 MC.6 Controls elsewhere draft a main-chat message
status: review
priority: medium
created: 2026-09-28T00:24:27.762212759Z
updated: 2026-09-29T04:29:33.246916965Z
started: 2026-09-28T01:57:27.861164601Z
tags:
    - muse
    - main-chat
    - frontend
parent: 72
claimed_by: lead
claimed_at: 2026-09-29T04:29:33.246737007Z
class: standard
---

Jobs page Edit, sessions list and connections page prefill the main-chat composer instead of opening a form; the change then goes through the usual card. RFC §6.9.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): 'Ask in main chat' on a job and on a session pre-fill the main chat's composer, unsent. Tests: SchedulePage.askInMainChat.test.tsx, MainChatMenu.test.tsx.
