---
id: 112
title: D6 Update the narrated explainer for setup.sh and the home step
status: review
priority: medium
created: 2026-09-29T00:29:52.323903929Z
updated: 2026-09-29T00:49:50.116273837Z
tags:
    - demo
claimed_by: onboarding-video
claimed_at: 2026-09-29T00:49:50.116272898Z
class: standard
---

The narrated explainer (kiro-crew-onboarding-explainer.mp4, 4:56) predates the one-command setup.sh, the gateway's 'Where should your crew live?' step with its size choice (Lite to Standard, per region), the collapsed first-run layout and the Lite home verified on real AWS. Re-narrate and re-render steps 1–3 and the home section to match, with the same voice and style.

[[2026-09-29]] Done: kiro-crew-onboarding-explainer.mp4 re-rendered whole (5:40; -16.1 LUFS); the old cut kept as -v1 (4:56) with its script. Changed: the user-flow map and step strip (home step as step 3), Step 1 (curl setup.sh | bash, no questions, fetch/build/PATH/start; --demo with the setup.sh --demo terminal), Step 2 (nav collapsed, privacy, then the gateway's home step card; kickoff facts include it), the home section (sizes per plan and region from home_size_options: Free Lite/Starter*/Standard, paid Lite/Economy/Small*/Standard; read-only plan and region, region picker, AWS sign-in or signup from the card, one-click Kiro sign-in of the home, the verified Lite numbers as reported, then move in/SC5), and status (QA.1, QA.2, RFC decisions). Unchanged lines reuse their cached narration. No new capture; the footage is from the D5 setup.sh --demo recording. README updated.
