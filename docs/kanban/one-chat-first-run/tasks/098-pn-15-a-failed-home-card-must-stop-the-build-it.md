---
id: 98
title: PN.15 A failed home card must stop the build it started
status: backlog
priority: medium
created: 2026-09-28T17:55:27.716975293Z
updated: 2026-09-28T17:55:27.716975293Z
tags:
    - parallel-nest
    - aws
    - bug
class: standard
---

Seen on the real-home run 2026-09-28: the card's watcher stopped (the job file could not be read) and showed failed, but the in-process launch worker kept creating the stack; the launch cancel only acted at the worker's next checkpoint about 9 minutes later, when it rolled back. The root cause (un-regioned Identity Center target) is fixed, but any watcher exit that fails the card while the worker runs should cancel the worker, or the card should keep watching the worker in memory.
