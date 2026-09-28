---
id: 85
title: Fresh install logs a deprecated-field warning in the Egg
status: review
priority: low
created: 2026-09-28T01:31:57.658234795Z
updated: 2026-09-28T15:53:43.833995041Z
tags:
    - bug
    - phase-1
parent: 17
claimed_by: lead
claimed_at: 2026-09-28T15:53:43.833728679Z
class: standard
---

A brand-new config warns about agent.subagent_cpu_cost_cores on first start, and the gateway's WARNING lines land in the start.sh terminal.

Fixed 2026-09-28: config/validation.py skips the deprecation notice when the stored value equals the field's default (_is_its_default; bool never matches a number). A save materializes every default, so a fresh home's first start wrote agent.subagent_cpu_cost_cores: 1.0 and the next load warned in the start.sh terminal. A changed value is still announced. Tests: TestIsItsDefault in test_config_validation.py; 667 passed with test_config_loader and test_telegram_accounts_deprecated. Spec: config.md.
