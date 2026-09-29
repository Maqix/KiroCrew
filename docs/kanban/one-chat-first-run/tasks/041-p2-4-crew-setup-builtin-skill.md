---
id: 41
title: P2.4 crew-setup builtin skill
status: review
priority: high
created: 2026-09-27T21:54:50.893615682Z
updated: 2026-09-29T04:29:32.278177305Z
tags:
    - phase-2
    - skill
parent: 17
claimed_by: lead
claimed_at: 2026-09-29T04:29:32.27790791Z
class: standard
---

SKILL.md guiding the first run: import first, one dev connector, preview-now job, keep it, stay on; light soul; card budget.

[[2026-09-27]] Sun 22:54
Backend implemented and unit-tested (test_setup_cards.py, test_setup_flow.py, test_setup_cards_api.py, test_setup_mcp_tools.py, test_secret_capture.py); waiting on the frontend card renderer and an end-to-end demo.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): src/kiro_crew/builtin_skills/crew-setup/SKILL.md (order of steps, card etiquette, main chat, hosted-assistant import, home step, sizes, channel). Tests: test_crew_setup_evals.py, test_setup_action_parity.py (skill table in step with the registry). Persona evals: evals/crew-setup/.
