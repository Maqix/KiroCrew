---
id: 107
title: PN.20 Per-region prices on the home card
status: review
priority: low
created: 2026-09-28T23:31:11.516088181Z
updated: 2026-09-29T00:04:51.81290076Z
tags:
    - parallel-nest
    - aws
class: standard
---

Sizes are priced from one us-east-1 figure. New accounts live in us-east-2, eu-north-1 or ap-southeast-2, where prices differ (ap-southeast-2 about 25% higher). Keep a small per-region price table, sourced from the public price list, and compare Starter against Small per region.


Done (uncommitted): sizes.ON_DEMAND_USD_PER_HR and GP3_USD_PER_GB_MONTH for us-east-1, us-east-2, eu-north-1 and ap-southeast-2, from AWS's public price list (published 2026-09-25), via sizes.region_prices. monthly_estimate_usd(key, region) and home_size_options(plan, region) price every option in the card's region, so the Starter-vs-Small rule is decided per region. An unknown region gets the us-east-1 figure, shown as 'about'. Small costs 51, 51, 53 and 65 dollars a month in those four regions. Spec: first-run.md 'Prices per region', and the RFC sizes paragraph.
