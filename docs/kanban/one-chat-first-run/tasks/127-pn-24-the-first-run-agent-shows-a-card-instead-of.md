---
id: 127
title: PN.24 The first-run agent shows a card instead of asking in prose
status: todo
priority: medium
created: 2026-09-29T20:06:42.165924108Z
updated: 2026-09-29T20:06:42.165924108Z
tags:
    - phase-2
    - prompt
    - evals
class: standard
---

Seen by the D8 recording driver in 2 of 3 takes at 9c07dfd9a: the hello asked 'want me to bring Hermes over?' in prose instead of proposing the import card, and after the import it asked 'which forge?' instead of proposing the connect card. The published take did not need that, but a user who answers in words gets a slower first run and the card's hash-bound consent is skipped until the agent proposes it. Expected: when the crew-setup skill has a card for the step, the agent proposes it in the same turn and asks at most one question beside it. Add a persona eval that fails on a prose-only offer for the import and connect steps.
