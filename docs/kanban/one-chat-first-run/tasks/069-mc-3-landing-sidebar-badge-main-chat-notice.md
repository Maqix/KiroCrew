---
id: 69
title: MC.3 Landing, sidebar badge, main_chat notice (frontend)
status: review
priority: high
created: 2026-09-28T00:06:11.461373862Z
updated: 2026-09-29T04:29:32.986206762Z
tags:
    - main-chat
    - frontend
parent: 66
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.985986517Z
class: standard
---

theme-boot main_slot; open it when no chat is named; Main badge first among pinned; render the main_chat notice.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): Frontend: landing on the main chat, the Main badge in the sidebar, the main_chat notice. Tests: ChatPage.mainLanding.test.tsx, ChatSidebar.mainChat.test.tsx.
