---
id: 93
title: 'EG.6 start.sh asks nothing: the home question moves into the Hello'
status: review
priority: high
created: 2026-09-28T14:39:06.31130024Z
updated: 2026-09-28T14:46:22.027679433Z
tags:
    - installer
    - home
    - skill
claimed_by: lead
claimed_at: 2026-09-28T14:46:22.027678011Z
class: standard
---

User direction 2026-09-28: no selections in the terminal; start.sh must land straight in the web chat. kirocrew start drops the home question, the AWS detection and the Kiro sign-in yes/no (a signed-out kiro-cli runs its own sign-in at once). --home stays for scripts, fresh installs only. The gateway's kickoff facts (setup_flow._aws_home_fact, cloud/local_signin.py: one read-only sts get-caller-identity) tell the Hello to offer a home in the signed-in account with its last four digits, region and monthly cost; yes proposes kind: home. No sign-in, no offer; move me to the cloud still works from any chat and the stay_on tip names it.

Verified live 2026-09-28 (isolated home, real kiro-cli, real read-only STS on the default account): start.sh printed no question and opened the chat; the Hello offered 'stay on this machine (free) or a home in the cloud in your AWS account (~$101/month)'; replying yes put the home card on screen (not clicked, nothing built). Tests: test_cli_start TestHomeChoice, test_local_signin (new), test_setup_flow home-offer tests, spawn-audit allowlist drops cli_start._run_interactive. Docs: RFC 5.1/5.2/5.7/6.8 rule 2, first-run.md, cli.md, install.md.
