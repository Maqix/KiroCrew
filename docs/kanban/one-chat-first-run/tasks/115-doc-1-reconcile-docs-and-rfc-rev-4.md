---
id: 115
title: DOC.1 Reconcile docs and RFC rev 4
status: review
priority: high
created: 2026-09-29T02:30:45.45106417Z
updated: 2026-09-29T03:01:24.881770425Z
tags:
    - docs
    - rfc
claimed_by: docs-rev4
claimed_at: 2026-09-29T03:01:24.881769268Z
class: standard
---

User direction 2026-09-29: update all the docs and the RFC now that building the prototype changed the approach. RFC to revision 4 with a 'What changed since revision 3' section; the owning spec first-run.md made coherent end to end; the install, getting-started, README, cli, cloud, session, config, mcp and context-management docs brought in line with the code; AGENTS.md gains a setup_actions/ row; indexes checked.

[[2026-09-29]] Tue 03:01
Done, not committed. RFC to revision 4 (checked at d91c296db): a 'What changed since revision 3' section (13 approach changes, each with its reason), superseded plans marked in place (the Hello's home offer, start.sh's kiro-cli offer and --service, the choice card, the fixed $101 tier, terminal AWS consent), open questions relabelled open / prototype answer / answered, and a 'Where the prototype stands' paragraph in §9. first-run.md restructured end to end: pieces table (start, sizes, handoff, first week, evals, simulated engine), the first-run session first, lifecycle, kinds, invariants, governance, guardrails (with one-at-a-time), adding a setup action, then 'The home' (step, AWS sign-in, size, region, prices, building, Kiro sign-in, move-in), main chat, first week, pasted secrets. Also install.md, windows-install.md (bundled kiro-cli contradiction), getting-started.md ('Your first chat', setup.sh), README, cli.md (landing path, --home, setup.sh section rewritten), cloud.md (local_signin and simulated_engine rows, real Lite numbers), session.md, config.md (lazy_background), mcp.md (setup tools), context-management.md, AGENTS.md (setup_actions/ row), RFC and modules index rows. Gates: docs-lint, comment-history, 1022 doc-related tests pass. Code mismatches for the lead: see the DOC.1 report (mark_stage('main') is a no-op; a home build is not resumed after a gateway restart; stale comments in cloud/sizes.py, cloud/local_signin.py, setup_cards.HOME_DEFAULT_SIZE; setup.sh defaults to the feature branch).
