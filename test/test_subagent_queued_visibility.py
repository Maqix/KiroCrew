"""A spawn the gate deferred is visible by id and by parent until it starts.

The deferred row lives only in the task store: no ``SubagentInfo``, no run
folder. ``queued_run_async`` / ``queued_runs_async`` are what the spawn status
and list routes read for it, so a caller told "queued" is not then told the run
does not exist.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from overload_fakes import mock_ctx, mock_sessions

import kiro_crew.subagent as subagent_mod
from kiro_crew.config.loader import KiroCrewConfig
from kiro_crew.resource_status import POSTURE_AMPLE, AdmissionDecision
from kiro_crew.subagent import QUEUED_REASON_LOW_MEMORY, SubagentManager
from kiro_crew.subagent_manager.admission import SpawnAdmissionCoordinator


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_a_deferred_spawn_is_readable_until_it_starts(monkeypatch) -> None:
    cfg = KiroCrewConfig()
    cfg.agent.subagent_cost_gb = 0.5
    cfg.agent.spawn_min_memory_gb = 4.0
    monkeypatch.setattr(KiroCrewConfig, "load", lambda: cfg)
    monkeypatch.setattr(subagent_mod, "Stats", MagicMock())
    monkeypatch.setattr(subagent_mod, "sel", MagicMock())
    monkeypatch.setattr(SpawnAdmissionCoordinator, "open_store_off_loop", True)
    monkeypatch.setattr(SpawnAdmissionCoordinator, "pump_off_loop", True)
    free = {"gb": 3.0}

    def memory_check(*, min_gb, **_kw):
        return free["gb"] >= min_gb, free["gb"]

    monkeypatch.setattr(subagent_mod, "check_memory_available", memory_check)
    monkeypatch.setattr(
        subagent_mod,
        "cached_admission_check",
        lambda: AdmissionDecision(admitted=True, posture=POSTURE_AMPLE, available_gb=32.0),
    )
    mgr = SubagentManager(sessions=mock_sessions(), ctx_builder=mock_ctx(), max_concurrent=3)
    await asyncio.wait_for(mgr.wait_taskq_ready(), 5)
    mgr._spawn_stagger_secs = 0.0
    # Long enough that the row is still parked when it is read below.
    mgr._taskq_admit_wait_secs = 30.0
    started = asyncio.Event()

    async def worker(info) -> None:
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(mgr, "_run", AsyncMock(side_effect=worker))
    try:
        info = await mgr.spawn_async("summarize the log", parent_session_key="dash:vis")
        assert info is not None and info.queued is True
        assert mgr.get(info.id) is None, "fixture: a deferred row has no registered run"

        queued = await mgr.queued_run_async(info.id)
        assert queued is not None
        assert queued.id == info.id
        assert queued.parent_session_key == "dash:vis"
        assert queued.task == "summarize the log"
        assert queued.accepted_at > 0
        assert queued.reason_detail == info.queued_reason_detail
        assert queued.reason_detail, "the gate's own sentence rides on the deferred event"
        # The kind is the parent's wait label, the one ``subagent_queued``
        # carries; it is read at call time, so a relabel is seen on the next read.
        mgr._queue_wait["dash:vis"] = {"reason": QUEUED_REASON_LOW_MEMORY}
        relabelled = await mgr.queued_run_async(info.id)
        assert relabelled is not None and relabelled.reason == QUEUED_REASON_LOW_MEMORY

        assert [q.id for q in (await mgr.queued_runs_async("dash:vis")).runs] == [info.id]
        assert [q.id for q in (await mgr.queued_runs_async(None)).runs] == [info.id]
        assert (await mgr.queued_runs_async("dash:vis")).truncated is False
        assert (await mgr.queued_runs_async("dash:other")).runs == ()
        assert await mgr.queued_run_async("0123456789abcdef") is None

        # Memory recovers; the row is claimed, registered and started. From then
        # on the registry answers for it, so the queued read must not.
        free["gb"] = 32.0
        mgr._taskq_admit_wait_secs = 0.05
        await mgr._taskq.run(mgr._taskq.defer, info.id, 0.0, reason="test: eligible now")
        for _ in range(250):
            if started.is_set():
                break
            mgr._drain_queue()
            await asyncio.sleep(0.02)
        assert started.is_set(), "the deferred row never started"
        assert mgr.get(info.id) is not None
        assert await mgr.queued_run_async(info.id) is None
        assert (await mgr.queued_runs_async("dash:vis")).runs == ()
    finally:
        mgr._shutting_down = True
        tasks = [task for task in mgr._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
        mgr._taskq.close()


def test_a_listing_past_the_cap_says_it_is_partial(tmp_path) -> None:
    """One row past the cap is read so a full page and a cut-off one differ: a
    truncated tail read as complete says accepted spawns were never accepted."""
    from kiro_crew import taskq
    from kiro_crew.subagent_manager.admission.taskq_bridge import (
        QUEUED_LISTING_CAP,
        _read_queued_rows,
    )

    store = taskq.TaskStore(tmp_path / "t.db").open()
    try:
        store.accept(
            taskq.TaskRecord(
                id=f"row{i:04d}",
                kind=taskq.KIND_SUBAGENT,
                session_key="dash:many",
                params={"task": f"t{i}"},
            )
            for i in range(QUEUED_LISTING_CAP)
        )
        rows, truncated = _read_queued_rows(
            store, agent_id=None, session_key="dash:many", exclude_ids=[]
        )
        assert len(rows) == QUEUED_LISTING_CAP and truncated is False, "exactly full is complete"

        store.accept_one(
            taskq.TaskRecord(
                id="row-last", kind=taskq.KIND_SUBAGENT, session_key="dash:many", params={}
            )
        )
        rows, truncated = _read_queued_rows(
            store, agent_id=None, session_key="dash:many", exclude_ids=[]
        )
        assert len(rows) == QUEUED_LISTING_CAP and truncated is True
        assert "row-last" not in {rec.id for rec, _ in rows}, "the oldest are the page"
    finally:
        store.close()
