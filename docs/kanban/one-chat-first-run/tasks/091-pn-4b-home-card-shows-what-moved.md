---
id: 91
title: PN.4b Home card shows what moved
status: review
priority: medium
created: 2026-09-28T05:37:26.183890186Z
updated: 2026-09-29T04:29:33.419060287Z
tags:
    - frontend
    - home
parent: 58
claimed_by: lead
claimed_at: 2026-09-29T04:29:33.418894924Z
class: standard
---

Frontend half of #62: localized copy for every move-in error code (setup_move_in.py), the failed move step's detail kept beside the retry, and a committed live move-in rendering from its outcome — where the chat now continues (Open your home via the crew switcher), schedules moved / kept here and why, whether settings moved, and the secret/connection names to set up again on the home.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): HomeMovedDetail.tsx: the home card lists what moved (jobs switched off here, the chat, secrets to enter again). Tests: SetupCardHomeMoveIn.test.tsx, test_setup_move_in.py.
