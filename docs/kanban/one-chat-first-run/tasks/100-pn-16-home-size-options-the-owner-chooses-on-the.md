---
id: 100
title: PN.16 Home size options the owner chooses on the card (Free vs Paid plan)
status: todo
priority: high
created: 2026-09-28T18:35:51.862167425Z
updated: 2026-09-28T18:35:51.862167425Z
tags:
    - parallel-nest
    - aws
    - home
    - frontend
class: standard
---

User direction 2026-09-28: give the owner options, explain them, and let them decide interactively in a good UI; research which machine size is enough for the agent. New AWS accounts start on the Free plan, whose EC2 only allows free-tier types (t3/t4g micro/small, c7i-flex.large, m7i-flex.large), so today's t4g.xlarge would be refused. Plan: read the account's plan (read-only); the home step shows 2–3 size options with plain explanations (what runs well, the monthly cost, whether the Free plan allows it) and marks the ones that need the paid plan, linking to the upgrade. The chosen size goes in with the Build click and the server checks it is one of the offered options. Sizing research is running (agent home-sizing); PN.5 measured ~0.6 GB for an idle gateway plus one chat, and the on-box dashboard build (6 GB heap) is what rules out small instances.
