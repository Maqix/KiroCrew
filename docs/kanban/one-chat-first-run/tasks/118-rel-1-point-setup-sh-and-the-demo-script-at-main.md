---
id: 118
title: REL.1 Point setup.sh and the demo script at main when the branch merges
status: todo
priority: medium
created: 2026-09-29T03:04:53.832587143Z
updated: 2026-09-29T03:04:53.832587143Z
tags:
    - installer
    - release
class: standard
---

setup.sh (_kc_branch and its header URL) and scripts/demo-first-run.sh hardcode feat/one-chat-first-run, so after a merge they would fetch the feature branch. Flip them to main in the merge PR, or derive the default branch from the URL the script was fetched from if that proves feasible; the docs already use a <branch> placeholder.
