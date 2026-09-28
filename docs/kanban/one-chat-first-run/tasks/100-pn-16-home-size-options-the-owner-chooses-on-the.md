---
id: 100
title: PN.16 Home size options the owner chooses on the card (Free vs Paid plan)
status: review
priority: high
created: 2026-09-28T18:35:51.862167425Z
updated: 2026-09-28T20:05:41.383737932Z
tags:
    - parallel-nest
    - aws
    - home
    - frontend
claimed_by: aws-signin-card
claimed_at: 2026-09-28T20:05:41.383737447Z
class: standard
---

User direction 2026-09-28: give the owner options, explain them, and let them decide interactively in a good UI; research which machine size is enough for the agent. New AWS accounts start on the Free plan, whose EC2 only allows free-tier types (t3/t4g micro/small, c7i-flex.large, m7i-flex.large), so today's t4g.xlarge would be refused. Plan: read the account's plan (read-only); the home step shows 2–3 size options with plain explanations (what runs well, the monthly cost, whether the Free plan allows it) and marks the ones that need the paid plan, linking to the upgrade. The chosen size goes in with the Build click and the server checks it is one of the offered options. Sizing research is running (agent home-sizing); PN.5 measured ~0.6 GB for an idle gateway plus one chat, and the on-box dashboard build (6 GB heap) is what rules out small instances.

The owner chooses the home's size on the card, with plain explanations, and the choice is checked against the account's plan.

Result (in review, uncommitted):
- `cloud/sizes.py`: new tier `starter` (m7i-flex.large, x86_64, 2 vCPU, 8 GB, 30 GB disk, ~$0.0958/h, about $72/month in us-east-1). It is the only tier with `free_plan_ok`. The template's Architecture=x86_64 path (AL2023 x86 AMI, node and kiro-cli x86 builds) covers it. The docstring and sizes tests now use the measured numbers.
- `cloud/local_signin.account_plan` (read-only `freetier get-account-plan-state`): FREE/PAID/unknown, plus credits_usd and expires. ResourceNotFoundException is treated as PAID, and upgrade-account-plan is never called. `vcpu_quota` reads Service Quotas L-1216C47A.
- `setup_cards.home_size_options(plan)`: offers [Starter, Standard(light)], each with key, label, instance_type, vcpu, ram_gb, monthly_usd and free_plan_ok, and credit_weeks when the Free plan's credits are known. Default is Starter unless the plan is PAID. The payload carries `size_options`, `size_default` and, when signed in, `plan`.
- Build posts `input.size`. `_chosen_home_size` refuses a size not offered (`home_size_not_offered`), a paid size on FREE (`home_size_needs_paid_plan`, with a link to AWS's plans page), and a size over the vCPU quota (`home_vcpu_quota_low`, with a link to Service Quotas). Spend-limit launch failures map to `home_spend_limit`.
- UI: radio options with explanation, cost, and a "Needs the paid plan" mark (plus the upgrade link on FREE); the header cost follows the selection; the selected size is posted with Build. Locale keys are in all 12 catalogs.
- crew-setup SKILL.md: the agent explains the card's options in plain words and never picks for the owner.
- Tests: test/test_home_sizes.py, test_cloud_sizes, website SetupCardHomeSizes.test.tsx (8).

Unverified: the real freetier API against a FREE account; that the Free plan's EC2 accepts m7i-flex.large in every home region; the exact wording AWS uses for a spend-limit refusal (matched by /spend(ing)? limit/i); per-region prices (one us-east-1 figure is shown).

Amendment (the user worried people won't agree to ~$100/month): the card leads with cheaper options.
- New tier `small` (`t4g.large`, arm64, 2 vCPU, 8 GB, $0.0672/h, about $51/month with disk). It is the paid plan's default.
- Per-plan offers are data: `setup_cards.HOME_PLAN_SIZES` gives FREE: starter (default) + light; PAID: small (default) + light, plus starter only when it is no dearer than small (never at today's prices). A plan not known yet gets the FREE list. `HOME_SIZE_OFFERS` gives each size a label and a note code (free_plan_credits / few_chats / many_chats).
- Options are sorted cheapest first. The heading is "Small · about $51/month", followed by the plain line and then the spec line.
- A future "Lite" (t4g.small, Free-plan eligible) would be a tier plus plan-list entries, plus its note copy. It is not added.
- Tests: test_home_sizes (PAID leads with small, a cheap free-plan size joins the paid list, the offers are data) and SetupCardHomeSizes (PAID view, headings).
- Unverified: per-region prices, since one us-east-1 figure is used for the starter-vs-small comparison.
