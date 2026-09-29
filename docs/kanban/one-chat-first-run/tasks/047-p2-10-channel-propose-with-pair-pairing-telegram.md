---
id: 47
title: P2.10 channel_propose with /pair pairing (Telegram first)
status: review
priority: medium
created: 2026-09-27T21:54:51.088716339Z
updated: 2026-09-29T04:29:32.507896259Z
tags:
    - phase-2
    - backend
parent: 17
depends_on:
    - 46
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.507432705Z
class: standard
---

Bot token via credential card; one-time /pair code allowlists the user's own id.

[[2026-09-27]] Sun 22:54
Deferred inside v1: the channel card commit answers 'not available yet' until the Telegram /pair hook exists in the transport.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): setup_actions/channel.py and dashboard/setup_channel.py: Telegram bot token typed into the card, one-time /pair code. The skill and tool description now describe it (the parity test found the gap). Tests: test_setup_channel.py, SetupCardChannel.test.tsx.
