---
id: 92
title: MC.9 The main chat hears when a handed-off chat finishes
status: review
priority: medium
created: 2026-09-28T06:12:50.084111971Z
updated: 2026-09-28T16:27:28.602577826Z
tags:
    - main-chat
    - backend
    - frontend
parent: 66
claimed_by: handoff-notice
claimed_at: 2026-09-28T16:27:28.602274119Z
class: standard
---

Demo finding: the main chat hands long work to its own chat (session_create + session_send) and promises to post the findings when it is done, but nothing tells it the chat finished, so the user has to ask.

[[2026-09-28]] Mon 06:47
Done: dashboard/handoff_notice.py posts one handoff_done system notice (meta {kind, slot, title}) in the main chat when a chat whose _created_by is the main chat ends a turn with a reply and is idle (nothing queued, no approval or question waiting, no plan or sub-agent running). No model turn, so no quota and no card (SC8). Hook: handoff_notice.note_cycle_end in chat_runner._finish_queue_cycle after chat_done; for a chat with no _created_by it returns without a disk read. At most one notice per child turn (turn generation), none while the main chat's newest row is already that chat's notice, none for the main chat itself; a notice due while the main chat runs waits for its cycle end. Frontend: HandoffDoneNotice (registry entry handoff_done ahead of system_notice) with Open <title> (onSessionOpen) and Ask for the result (onSendAsUser -> ChatPage send, sends What did <title> find? as the user); SystemNoticeRow draws it without actions elsewhere; keys pages.chat.handoffDoneNotice.* in all locales. crew-setup and the crew overview no longer promise an unprompted report. Tests: test/test_handoff_notice.py, website/src/test/HandoffDoneNotice.test.tsx; spec first-run.md, RFC 6.9. Not verified: a live gateway run end to end.

[[2026-09-28]] Mon 16:19
Follow-up, the three gaps closed. (1) Error: a handed-off turn that ends on an error row with no reply posts the same handoff_done notice with meta outcome error (English fallback: stopped with an error, open it to see what happened); the dashboard draws it as a warning with Open only. A turn with a stop_event row (every Stop press and session_stop writes one) posts nothing. Done notices now carry outcome done; dedupe is per chat and outcome. (2) Plan end: _stage_loop's idle close calls handoff_notice.note_controller_end, which runs note_cycle_end from the plan task's done callback (the controller keeps the slot reserved until then), so held notices flush when a plan ends; a plan paused on the user is not reported done. (3) Restart: held notices are mirrored to handoff_owed in the first-run state file (at most 20 per chat, single ordered writer); server.py calls handoff_notice.restore_held after the session restore, which drops holds for a former main chat, re-bounds titles, dedupes against the newest row, and keeps the hold when the main chat is busy or not open. Tests: test/test_handoff_notice.py 34 pass (a plan-end integration through the real _stage_loop, verified to fail with the hook removed); frontend HandoffDoneNotice error variant; new locale key pages.chat.handoffDoneNotice.failed in all catalogs. Not verified: a live gateway restart; the server.py wiring is pinned only by a source-order test.

Lead follow-up 2026-09-28: the first-run state file had several unlocked read-then-write writers (stages, main, first_week, handoff_owed). first_run.update_state now serializes read-change-write under one in-process lock; every writer uses it. test/test_first_run_state.py: 8 concurrent writers keep every key (fails with the lock removed; checked). 143 passed across first_run_state, first_week, handoff_notice, setup_flow, setup_guardrails, cli_start.
