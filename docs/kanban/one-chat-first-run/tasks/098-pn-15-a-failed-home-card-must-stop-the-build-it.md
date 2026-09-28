---
id: 98
title: PN.15 A failed home card must stop the build it started
status: review
priority: medium
created: 2026-09-28T17:55:27.716975293Z
updated: 2026-09-28T20:16:41.41722684Z
tags:
    - parallel-nest
    - aws
    - bug
claimed_by: lead
claimed_at: 2026-09-28T20:16:41.417225778Z
class: standard
---

Seen on the real-home run 2026-09-28: the card's watcher stopped (the job file could not be read) and showed failed, but the in-process launch worker kept creating the stack; the launch cancel only acted at the worker's next checkpoint about 9 minutes later, when it rolled back. The root cause (un-regioned Identity Center target) is fixed, but any watcher exit that fails the card while the worker runs should cancel the worker, or the card should keep watching the worker in memory.

Done 2026-09-28: setup_flow._watch_home stops a build it cannot follow. Three unreadable or missing job reads in a row, or the watcher crashing, set the launch's cancel event (the worker rolls the stack back at its next checkpoint) and fail the card with home_build_untracked (_stop_untracked_build). One bad read is tolerated. Tests: TestAnUntrackedBuild in test_home_signin.py (missing, unreadable, one bad read, crash); 107 passed with the home suites. Spec: first-run.md. Localized copy for the new code: queued with aws-signin-card; until then the card shows the English server message.
