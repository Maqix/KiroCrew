---
id: 83
title: MC.8 spawn_run rejected in the first-run chat
status: review
priority: medium
created: 2026-09-28T01:31:57.561018888Z
updated: 2026-09-29T04:29:33.316427811Z
started: 2026-09-28T02:03:18.238502212Z
tags:
    - main-chat
    - bug
parent: 66
claimed_by: lead
claimed_at: 2026-09-29T04:29:33.316151147Z
class: standard
---

Demo finding: the agent fell back to spawn_run, which answered 'spawn rejected' with no output. Find which gate refused it (governance, subagent cap on a host without cgroup v2, or approval).

[[2026-09-28]] Mon 02:24
Finding: the bare 'spawn rejected' is written by the spawn approval gate, subagent_manager/admission/pump.py _spawn_with_approval_impl (the not-approved branch), whenever the approval callback returns False or raises. Not governance, not the subagent cap, not cgroup v2 (sandbox.py only logs a warning there), and nothing first-run specific: a dashboard-parented spawn with no Trust/auto-approve goes to the dashboard approval prompt (slack/gateway.py _interactive_approval -> DashboardState.request_approval). Real bug found: ApprovalCoordinator.request (dashboard/interaction_coordinator.py) answers a cancellation of the waiting task with False, so a Stop, a reaper reap or a gateway shutdown of a spawn still parked on its prompt was recorded and reported to the parent as 'spawn rejected' (over the neutral stop / the reap's 'awaiting an unanswered spawn approval'). Reproduced against the real coordinator wait. Fix: the gate checks asyncio.current_task().cancelling() and re-raises the cancel so the stopper owns the record. The remaining refusals now name the gate and next step: SPAWN_DECLINED_ERROR (prompt declined or expired unanswered) and SPAWN_APPROVAL_FAILED_ERROR (callback raised, audited reason=approval_error), both keeping the 'spawn rejected' prefix and naming no bypass setting. Which of decline/expiry/stop/restart happened in the demo is unverified (no demo log available). Tests: test/test_spawn_rejection_names_its_gate.py; spec: subagent.md 'A refusal names its gate; a stop is not a refusal'.

Result (board audit 2026-09-29, branch feat/one-chat-first-run): spawn_run's refusal names its gate so the agent can explain it. Tests: test_spawn_rejection_names_its_gate.py.
