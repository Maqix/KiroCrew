---
id: 101
title: 'P2.18 First-run chat opens full width: nav rail and session list collapsed'
status: review
priority: medium
created: 2026-09-28T18:37:13.046213683Z
updated: 2026-09-28T18:41:58.396402021Z
tags:
    - phase-2
    - frontend
class: standard
---

User direction 2026-09-28, after testing the first run on a laptop: when the one-chat first run opens, the left nav starts as its icon rail and the session list starts hidden, so the conversation takes the full width with the chat header and the sessions toggle at the top left. Only the first-run experience; desktop widths only; existing installs and non-first-run sessions keep today's layout; a user expand persists and wins. Reuse the existing collapse state and toggles (`mc-nav`, `mc-sidebar-pinned`), no new layout mode.

[[2026-09-28]] Mon 18:41
Done, not committed. A desktop page load that opens on the first-run chat before graduation starts with the rail collapsed and the session list hidden. The verdict is decided once per load (website/src/hooks/useFirstRunLayout.ts, settled by App, read by ChatPage) and never persisted. The existing toggles write mc-nav / mc-sidebar-pinned, and a stored value wins. Other sessions, other landing pages, mobile, existing installs and the main chat after graduation keep today's layout. Tests: useFirstRunLayout.test.ts, App.firstRunLayout.test.tsx, ChatPage.firstRunLayout.test.tsx; all 130 App*/ChatPage* suites green. Spec: first-run.md (The first-run session), learn-cron-dashboard.md (Nav sidebar). dist rebuilt and synced. Not seen in a real browser.
