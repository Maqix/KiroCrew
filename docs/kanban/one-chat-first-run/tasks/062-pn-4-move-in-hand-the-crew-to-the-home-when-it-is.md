---
id: 62
title: 'PN.4 Move in: hand the crew to the home when it is healthy'
status: review
priority: high
created: 2026-09-27T22:54:57.376927319Z
updated: 2026-09-28T05:12:48.757252823Z
tags:
    - parallel-nest
    - backend
parent: 58
depends_on:
    - 61
claimed_by: claude
claimed_at: 2026-09-28T05:12:48.756891453Z
class: standard
---

Verified end to end on 2026-09-28 against a real EC2 home in the default account (stack kirocrew-kc-26dbef): reach → pack → carry → chat all done; 2 prompt jobs switched off here and running on the home; the chat copied (27 messages); GITHUB_TOKEN named for re-entry. Found: Remote Crew (instances.enabled) was off by default, so Move in now turns it on and restarts once (move_in_restarting). Follow-ups: moved jobs' session key → the moved chat (move-in agent), home card rendering of what moved (PN.4b), home sign-in by copying this machine's Kiro sign-in (PN.10).
