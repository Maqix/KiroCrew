---
id: 120
title: P2.21 Ask where the crew lives after the first kept job; compact home card
status: review
priority: high
created: 2026-09-29T15:17:57.48334976Z
updated: 2026-09-29T16:17:43.15325494Z
tags:
    - phase-2
    - parallel-nest
    - frontend
    - ux
class: standard
---

User feedback 2026-09-29: the home card arrives right after privacy with too little context, and it's too big. Decision (the user): ask after the first job is kept, merged with 'keep running'. A compact choice card offers 'This machine: free, runs while it's on' or 'In the cloud: from $N/mo, always on'. Cloud leads to the AWS steps (signed-in line, or sign-in/create account), then a compact size list (one line per size: name · RAM · $/mo · badge; trade-offs behind a details toggle), then Build. This machine leads to the stay-on service offer. The gateway shows the choice card itself at graduation if the agent hasn't, so the step always happens. --home cloud keeps the early card.


Done (uncommitted):
- Privacy shows a home card only for --home cloud (_show_chosen_home).
- At the first kept job, _commit_cron calls _offer_home_choice once graduate() graduates the chat, unless a home card exists or a script answered --home. It shows a compact choice card (offer, step choose, from_usd, no AWS call).
- The claimed choose decision on the home action: 'here' commits with stayed, and the result asks the agent to offer the service card. 'cloud' re-issues the same card via replace_payload (new hash, phase build).
- The build card is compact: an AWS meta line, one row per size with recommended / needs-paid badges, the trade-offs behind 'What's the difference?', a small Simulated badge, and no cost line.
- SKILL.md order: Hello, then bring & connect, then preview & keep, then where the crew lives. _home_step_fact is gone; the kept job's result text announces the question.
- Tests: test_setup_flow TestWhereTheCrewLives (6) plus the privacy test; SetupCardHomeOffer and SetupCardHomeSizes rewritten.
- 14 new keys in 12 catalogs; 6 dead keys removed.
- Docs: first-run.md, and RFC §5, §5.2, §5.5, §5.7, §6.8 plus What changed item 14.
