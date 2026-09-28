---
id: 99
title: 'PN.14 A built home that never signed in to Kiro: sign it in before Move in'
status: review
priority: medium
created: 2026-09-28T18:18:27.832371372Z
updated: 2026-09-28T18:18:27.832371372Z
tags:
    - parallel-nest
    - backend
    - frontend
parent: 58
claimed_by: aws-signin-card
claimed_at: 2026-09-28T18:18:27.832385571Z
class: standard
---

Found in the live-89 real-home run: when the Kiro sign-in wait ran out (or a restart cut it short), the build went DONE with STEP_SIGNIN skipped and signin_detected false. The card still offered Move in to a home whose agent can't answer.

Result (in review, uncommitted):
- `setup_flow._home_built`: DONE and not signed in (and not simulated) sets phase "signin" and leaves the card pending, with outcome {ready: false, needs_signin: true, steps, signin when the job still holds one}. Signed in goes to phase "move" as before.
- `setup_flow._sign_home_in`: the commit in phase "signin" calls `handlers_cloud.restart_signin(state, job_id)`, the route's extracted body; the route is now a thin wrapper. Every refusal is a card error, and "already signed in" goes to Move in. It then goes waiting and re-watches with may_open from the click's fresh browser_is_here. The phase lives on the stored card, so the button survives a restart.
- (A) The copy for the lead's `home_identity_region_unknown` refusal, plus `home_signin_unavailable`, `launch_job_not_found`, `launch_has_no_instance` and `login_target_unreadable`.
- Frontend needs-sign-in view with "Sign the home in to Kiro"; locale keys in all 12 catalogs.
- Tests: test/test_home_needs_signin.py (10), a restart_signin test in test_cloud_signin_recovery.py, and website SetupCardHomeNeedsSignin.test.tsx (4).
- Docs: first-run.md home row and "The home's Kiro sign-in".
