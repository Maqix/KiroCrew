---
id: 58
title: 'Parallel nest: build the home in the background during the first run'
status: review
priority: high
created: 2026-09-27T22:54:57.25782185Z
updated: 2026-09-28T23:30:43.188122331Z
tags:
    - epic
    - parallel-nest
claimed_by: lead
claimed_at: 2026-09-28T23:30:43.188121613Z
class: standard
---

From the team thread: the first run never waits for infrastructure. In the Egg the user may pick 'my AWS account'; the AWS CLI's own sign-in runs, the cost is shown and consented, and the existing launch engine builds the home as a background job while the chat proceeds locally. A progress card shows it; a Move in card hands the crew over when healthy. A managed-hosting provider plugs into the same RemoteProvisioner seam later.

Board sweep 2026-09-28: PN.1–PN.18 are built and pushed (feat/one-chat-first-run). The home is its own first-run step, with AWS sign-in and account creation from the card, a size choice per plan (Lite, Economy, Small, Starter, Standard), region and plan detection, the home's own one-click Kiro sign-in, move-in, the prebuilt dashboard, and an untracked build stopped. Still open: PN.19 region picker, PN.20 per-region prices, PN.21 chat-session cap, and a real Lite boot (running now).
