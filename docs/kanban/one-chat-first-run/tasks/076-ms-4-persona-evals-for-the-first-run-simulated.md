---
id: 76
title: MS.4 Persona evals for the first run (simulated engine + rubric)
status: review
priority: medium
created: 2026-09-28T00:24:27.7259626Z
updated: 2026-09-29T04:29:33.211180021Z
started: 2026-09-28T02:27:45.67716495Z
tags:
    - muse
    - measurement
    - phase-2d
parent: 72
claimed_by: lead
claimed_at: 2026-09-29T04:29:33.210886102Z
class: standard
---

A model plays the user from a persona file; rubric: cards before first kept job, minutes to first preview, no card outside a person-started turn, no paste-a-secret ask, no invented link. Seed: the demo driver. RFC §8.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): evals/crew-setup/ (cases.json, run_evals.py, fixtures) with scripted personas on the simulated engine and a rubric. Tests: test_crew_setup_evals.py.
