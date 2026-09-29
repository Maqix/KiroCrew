---
id: 82
title: 'MC.7 Q13: mount kirocrew-dashboard for the main chat (session tools)'
status: review
priority: high
created: 2026-09-28T01:31:57.50828752Z
updated: 2026-09-29T04:29:33.282269708Z
tags:
    - main-chat
    - open-question
parent: 66
claimed_by: lead
claimed_at: 2026-09-29T04:29:33.282084952Z
class: standard
---

Demo finding: the default agent has no session_create/session_send (kirocrew-dashboard is opt_in), so the main chat cannot hand work to its own chat. Options in RFC Q13.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): The first-run chat runs on the kirocrew-main agent spec (agent_files.py), which mounts kirocrew-dashboard with session_create and session_read_message granted. Tests: test_main_chat_agent.py.
