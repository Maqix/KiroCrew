---
id: 117
title: PN.22 Resume a home build after a gateway restart
status: in-progress
priority: high
created: 2026-09-29T03:04:53.799698784Z
updated: 2026-09-29T03:05:31.934703478Z
tags:
    - parallel-nest
    - backend
claimed_by: aws-signin-card
claimed_at: 2026-09-29T03:05:31.934702114Z
class: standard
---

Found by the docs sweep 2026-09-29: after a gateway restart nothing re-arms setup_flow._watch_home, reap_orphans settles the launch job, and the home card stays 'waiting' for ever. Revision 3 promised that a build carries on and the card picks up where it was. On boot, for each home card still waiting with a job_id: re-attach a watcher if the job is still live (or re-drive it the way the Instances hub does), or else settle the card with the job's real outcome (failed, done unsigned, done). Tests for each case.
