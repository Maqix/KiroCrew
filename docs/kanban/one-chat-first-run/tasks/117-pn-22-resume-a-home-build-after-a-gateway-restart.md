---
id: 117
title: PN.22 Resume a home build after a gateway restart
status: review
priority: high
created: 2026-09-29T03:04:53.799698784Z
updated: 2026-09-29T03:46:11.792910207Z
tags:
    - parallel-nest
    - backend
class: standard
---

Found by the docs sweep 2026-09-29: after a gateway restart nothing re-arms setup_flow._watch_home, reap_orphans settles the launch job, and the home card stays 'waiting' for ever. Revision 3 promised that a build carries on and the card picks up where it was. On boot, for each home card still waiting with a job_id: re-attach a watcher if the job is still live (or re-drive it the way the Instances hub does), or else settle the card with the job's real outcome (failed, done unsigned, done). Tests for each case.


Done (uncommitted): setup_flow.resume_home_builds runs at boot in dashboard/server.py, right after ensure_first_run_session. It runs the store's once-per-process reap (handlers_cloud._astore / reap_orphans), then re-attaches _watch_home with may_open=False for every home card still waiting with a private job_id. The existing watcher settles the card at its first poll. Failed gives home_build_failed with the job's reason. Done and signed in gives pending/ready. Done but unsigned gives phase signin with needs_signin. An unreadable or missing job gives home_build_untracked. A job this process still drives is followed. _start_home_watch keeps one watcher per card and is used by all three start sites. Tests: test/test_home_resume.py (8). Docs: first-run.md 'Building the home', and the RFC in three places.


Cost-safety follow-up (uncommitted):
- A failed card for a build a restart cut short after its stack began (launch_job.interrupted_by_restart plus stack_may_exist) carries outcome.leftover {tag, stack, region}. It says 'Kiro Crew lost track of this home's build when it restarted, so parts of it may still be in your AWS account and billing. Remove them here, or from Remote Crew.'
- An untracked card is worded from outcome.stopped: true keeps today's text; false gets the same sentence ending 'Remove them from Remote Crew.' (no tag is known there). The server's message, which the agent reads, matches.
- 'Remove what it created' is the home action's remove decision (claimed=False). It checks governance, the hash, the job again (failed card, interrupted job, stack may exist) and input.tag against the job's tag. It claims under the store lock, so it runs once. It runs handlers_cloud.teardown_stack, the Instances hub's destroy (ec2.destroy then _teardown_after_delete) waited on, in the background. The card shows it active, then done ('Removed: AWS confirms the stack ... is gone.') or failed with the button again.
- Tests: test_home_leftover.py (15), SetupCardHomeLeftover.test.tsx (7), and a parity check that SetupDecision equals setup_cards.DECISIONS.
