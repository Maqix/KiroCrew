---
id: 108
title: PN.21 Cap live chat sessions by memory on small homes
status: review
priority: medium
created: 2026-09-28T23:31:11.549035666Z
updated: 2026-09-29T00:18:55.525059914Z
tags:
    - parallel-nest
    - perf
class: standard
---

Card #103 item 7: chat sessions have no count cap, so a Lite home (2 GB) can run out of memory. Size a cap from host memory, like compute_max_subagents does for sub-agents, and release the oldest idle session first.

[[2026-09-29]] Tue 00:18
Done (uncommitted, for review). New key session.max_live_sessions (default 0 = auto, hot: read off the manager's _cfg at each open, no restart). Auto = (total RAM - 1536 MiB) // 400 MiB per chat, clamped [2, 64]: 2 on Lite 2 GB, 5 on Economy 4 GB, 15 on 8 GB, ~35 on 16 GB, 64 from 32 GB; unreadable total = 64. Sized from TOTAL memory, not available (a boot snapshot would double-count live chats and pin a busy laptop). SessionManager.get_or_create -> _make_room_for -> SessionCleanup.make_room releases the LRU idle chat(s) with the idle sweep's own release (on_session_expire + reset expect_session/skip_if_busy/skip_if_injecting, no clear_conversation) so the chat resumes via session/load. Never released: turn in flight, attached sub-agent work or pending injection, the main chat or a chat with a question card/coordinator approval (dashboard keep-live probe, chat_utils.wire_session_keep_live_probe), _bg/_hb and stateless namespaces (not counted either). Nothing releasable -> the chat opens, WARNING logged. Files: session_live_cap.py (new), session.py, session_cleanup.py, config/sections.py, config/loader.py, dashboard/chat_utils.py, dashboard/server.py, test/test_session_live_cap.py (43 tests), session.md, config.md, configuration.md, config-baseline.json. Not verified: a real Lite/Economy boot with many chats; macOS/Windows (host_total_mib covers both, untested there).
