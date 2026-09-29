---
id: 111
title: P2.19 No generic dashboard tips during the first run
status: review
priority: medium
created: 2026-09-29T00:07:27.038267728Z
updated: 2026-09-29T00:22:40.654153777Z
tags:
    - phase-2
    - frontend
class: standard
---

Seen in the D5 recording 2026-09-29: the dashboard's generic tip banner ('Store credentials in the encrypted vault…') pops up over the first-run chat, right under the home step card. During the first run the setup cards and the first-week notices are the only guidance. Hold the generic tips while the first run is in progress (first_run_slot, no main_slot yet), and let them resume after graduation. The user's own 'Turn off tips' still wins.

[[2026-09-29]] Tue 00:22
Done, not committed. ChatPage folds firstRunInProgress(firstRunSlot, mainSlot) into tipBlocked, the useTipTrigger block temporary sessions already use. Tips are held in every chat, with no fetch and no 'shown' so the cadence isn't spent, and resume live when graduation sets main_slot; the opt-out is server-side and still wins. The predicate lives in hooks/useFirstRunLayout.ts and the layout verdict now uses it too. Tests: ChatPage.firstRunTips.test.tsx (held in first-run and other chats, released mid-session on graduation, shown after graduation, existing install unchanged; fails with the hold removed), TipCard.test.tsx (lifting the block mid-turn re-arms the 10s gate, nothing spent while held), useFirstRunLayout.test.ts. 71 ChatPage*/Tip* suites green. Specs: first-run.md, learn-cron-dashboard.md (Tips). dist rebuilt and synced. Not seen in a browser.
