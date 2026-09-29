---
id: 113
title: 'P2.20 Setup actions as a registry: one module per action, a parity test'
status: review
priority: high
created: 2026-09-29T00:32:48.637197058Z
updated: 2026-09-29T01:42:13.38201995Z
tags:
    - phase-2
    - backend
    - frontend
    - architecture
claimed_by: setup-registry
claimed_at: 2026-09-29T01:42:13.38201948Z
class: standard
---

User direction 2026-09-29: the onboarding actions must be modular and extensible. Today each card kind is spread over the card store's validators, ~50 kind branches in dashboard/setup_flow.py, the setup_card MCP schema, SCOPE_CATALOG, ~13 frontend branches, copy keys and the crew-setup skill's tool table, and nothing catches a missed layer. Plan: a SetupAction registry (kind, governance scope, build, commit, extra decisions, flags such as gateway-only, budget-exempt and stack-exempt, argument schema), one module per action, setup_flow dispatching through it, the MCP schema generated from it, a frontend card registry with a generic fallback body, a parity test that fails when a kind misses a layer, and an 'Adding a setup action' guide in first-run.md. Behaviour-preserving.

[[2026-09-29]] Tue 01:39
Result: src/kiro_crew/setup_actions/ (base.SetupAction + Decision, one module per kind, ACTIONS registry); setup_flow.propose/decide dispatch through it (no kind branches left in dispatch; committers/watchers stay in setup_flow, wrapped by lazy import); setup_card schema/enum/description generated from it (inputSchema byte-identical to before). Frontend: website/src/components/setup/setupCardRegistry.tsx (SETUP_CARD_KINDS, SETUP_CARD_TITLE_KEY, FallbackBody for unknown kinds with new key components.setupCard.fallback_approve in all catalogs). Tests: test/test_setup_action_parity.py (store, SCOPE_CATALOG, TS registry, en.manual title keys, SKILL.md rows, both directions), test/test_setup_actions.py (gateway-only unreachable; hash + governance hold for every kind/decision; schema golden), SetupCard.test.tsx fallback, setupCardRegistry.test.ts. Model-facing delta: channel row in crew-setup SKILL.md and a channel clause in the tool description (parity gap). Docs: first-run.md 'Adding a setup action'. Pre-existing, not from this card: bnStyle.test.ts (9 > 8 Bengali-digit values, from bdda1c534) and the comment-history gate flag in session_live_cap.py:27.

[[2026-09-29]] Tue 01:42
Lead confirmed the channel gap fix. CHANNELS holds telegram only, so the skill row reads channel: "telegram" (the one channel wired); the tool-description clause is generated from CHANNELS labels ('a Telegram bot the user reaches you through; ...'). Added one crew-setup card-etiquette line on when to propose it (the user wants to reach the crew from their phone; token into the card, then the one-time /pair code).
