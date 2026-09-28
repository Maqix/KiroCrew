---
id: 84
title: 'PN.9 EC2 home install fails: on-box frontend build'
status: done
priority: high
created: 2026-09-28T01:31:57.601101409Z
updated: 2026-09-28T05:12:33.801238197Z
started: 2026-09-28T05:12:33.801237009Z
completed: 2026-09-28T05:12:33.801237009Z
tags:
    - nest
    - bug
parent: 58
claimed_by: claude
claimed_at: 2026-09-28T05:12:33.801238078Z
class: standard
---

Root cause (confirmed from a kept debug stack, kirocrew-kc-da972f, deleted after): with edited tracked files the launcher ships the working tree's TRACKED files only (by design, so an untracked secret never leaves the machine), so an edited ChatPage.tsx arrived without the new, never-added components it imports; tsc failed on the instance after ~15 min. Fix: build_source_tarball now refuses such a tree before anything is created, naming the files and the fix (git add). Tests in test/test_cloud_source.py; cloud.md updated.
