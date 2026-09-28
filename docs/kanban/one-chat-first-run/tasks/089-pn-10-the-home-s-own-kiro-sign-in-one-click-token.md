---
id: 89
title: PN.10 The home's own Kiro sign-in, one click (token copy is a later spike)
status: review
priority: high
created: 2026-09-28T05:09:29.14318187Z
updated: 2026-09-28T16:43:52.606301773Z
tags:
    - parallel-nest
    - auth
    - security
parent: 58
claimed_by: aws-signin-card
claimed_at: 2026-09-28T16:43:52.606140627Z
class: standard
---

Re-scoped by the user (2026-09-28): the home keeps its OWN Kiro sign-in (no token copy), made one click. Reason: copying puts one long-lived refresh token on two machines, and refresh rotation risks signing the local kiro-cli out. Token copy stays a later spike (RFC §6.8 rule 5, Q15).

Result (in review, uncommitted):
- `dashboard/home_signin.py`: the home card's Build click records `private.browser_is_here` (setup_aws_signin.browser_is_here); only that click's watcher gets `may_open`. When the build shows `signin: {url, code}`, the gateway opens the URL once per code via `cloud.login._open_browser`. That happens only for a non-simulated home, on HTTPS, and on a Kiro sign-in host (*.awsapps.com, device.sso.<region>.amazonaws.com, *.kiro.dev) or the login target's IdC start-URL host. A watcher resumed after a restart never opens. kiro-cli's URL is verification_uri_complete (user_code prefilled; parse_login_output prefers it), and the card keeps the code beside the link.
- One `home_signin` system notice ({kind, opened, card}) in the chat that owns the card. It is not a model turn and waits while that chat is mid-turn. The frontend (`pages/chat/HomeSigninNotice.tsx`, via SystemNoticeRow) shows localized copy in all catalogs.
- Card copy: the home's own sign-in, each machine keeps its own, and a browser already signed in needs one click.
- Docs: RFC §6.8 rule 5 + Q15, first-run.md "The home's Kiro sign-in".
- Tests: test/test_home_signin.py (25), HomeSigninNotice.test.tsx, and SetupCardHomeAwsSignin.test.tsx (copy).

Unverified: a real kiro-cli device flow on a real home (URL shape taken from parse_login_output fixtures and kiro-cli's printed "Open this URL"), and that a social (Google/GitHub) device flow's page lives on *.kiro.dev. Otherwise it stays a card link.

Later spike (the original plan): one copied token on two machines for a day per identity type; gateway-only read of the harness store and one send over the SSM tunnel (never model, transcript, card, logs, export: SC2/SC4); a separate consent line and 'sign the home out'. Security guidance prefers short-lived, rotated credentials.
