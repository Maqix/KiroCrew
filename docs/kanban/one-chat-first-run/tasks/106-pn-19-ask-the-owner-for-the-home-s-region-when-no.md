---
id: 106
title: PN.19 Ask the owner for the home's region when no region answers
status: review
priority: medium
created: 2026-09-28T23:31:11.480672793Z
updated: 2026-09-29T00:04:51.810201715Z
tags:
    - parallel-nest
    - aws
    - frontend
class: standard
---

resolve_home_region returns '' when neither the profile's region nor the three new-account home regions answer. The card then keeps its region and the build may fail. Show a region picker on the card in that case, and re-issue the payload hash with the owner's pick.


Done (uncommitted): when no region answers, the payload has region_unknown and region_choices (local_signin.HOME_REGIONS, the 17 commercial regions with no opt-in), preselecting the profile's region. The card shows a native select, the copy 'AWS didn't say which region this account uses. Pick the one shown in your AWS console.' and 'Use this region', which is the region decision. setup_flow._decide_home_region checks the card, the hash, governance and sc.validate_home_region (regex plus HOME_REGIONS), then probes that one region read-only (local_signin.probe_region), re-reads the plan, and re-issues the card with replace_payload. A pick that answers drops the picker. A pick that does not answer is kept, with the picker and home_region_no_answer, and Build is offered for it. Tests: test_home_region.py, SetupCardHomeRegion.test.tsx. Spec: first-run.md 'Asking for the region'.
