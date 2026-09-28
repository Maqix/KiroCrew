---
id: 58
title: 'Parallel nest: build the home in the background during the first run'
status: todo
priority: high
created: 2026-09-27T22:54:57.25782185Z
updated: 2026-09-27T22:54:57.25782185Z
tags:
    - epic
    - parallel-nest
class: standard
---

From the team thread: the first run never waits for infrastructure. In the Egg the user may pick 'my AWS account'; the AWS CLI's own sign-in runs, the cost is shown and consented, and the existing launch engine builds the home as a background job while the chat proceeds locally. A progress card shows it; a Move in card hands the crew over when healthy. A managed-hosting provider plugs into the same RemoteProvisioner seam later.
