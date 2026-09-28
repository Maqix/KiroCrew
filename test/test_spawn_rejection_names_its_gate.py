"""A refused spawn says which gate refused it, and a stopped one is not refused.

Reported from the first-run chat: the agent called ``spawn_run`` and got back
``spawn rejected`` and nothing else, so it could not tell a person saying No
from a prompt nobody answered, from a failure, from a Stop. Two things follow.

* The dashboard's approval wait (``ApprovalCoordinator.request``) answers a
  cancellation of the waiting task with ``False``. A Stop, a reap or a gateway
  shutdown of a spawn still parked on its prompt therefore reached the spawn
  gate as if a person had declined it: the gate wrote ``spawn rejected`` over
  the neutral stop and over the reap's own cause, and reported that to the
  parent. These tests drive the REAL coordinator wait, because a test double
  that lets the cancellation through cannot show the defect.
* The refusals the gate still owns name the gate, the reason and what the
  agent can do, and still start with ``spawn rejected`` for the readers that
  match on it (``spawn_progress_summary``, the workflow memory scenario).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kiro_crew.dashboard.interaction_coordinator import ApprovalCoordinator
from kiro_crew.subagent import SubagentInfo, SubagentManager
from kiro_crew.subagent_manager.admission.pump import (
    SPAWN_APPROVAL_FAILED_ERROR,
    SPAWN_DECLINED_ERROR,
)

pytestmark = pytest.mark.usefixtures("healthy_host_memory")

#: Settings an agent could use to lift the gate. The completion event is
#: automation input, so no refusal may carry them (see the no-surface refusal).
_BYPASS_RECIPES = (
    "config.json",
    "auto_approve_subagent_spawn",
    "auto_approve_sources",
    'approval_mode="auto"',
)


def _sessions() -> MagicMock:
    """SessionManager double with a default install's posture: no session trust."""
    sessions = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_or_create = AsyncMock()
    sessions.release = MagicMock()
    sessions.reset = AsyncMock()
    sessions.get_agent = MagicMock(return_value="")
    sessions.get_agent_selection = MagicMock(return_value=("template", ""))
    sessions.get_approval_policy = MagicMock(return_value="ask")
    return sessions


def _ctx_builder() -> MagicMock:
    ctx = MagicMock()
    ctx.hooks.auto_approve_subagent_spawn = False
    return ctx


def _dashboard_state() -> SimpleNamespace:
    """The attributes ``ApprovalCoordinator.request`` reads on the dashboard state."""
    return SimpleNamespace(
        _approval_futures={},
        _pending_approvals={},
        broadcast_ws=lambda *_a, **_k: None,
        push_slots_update=lambda: None,
        _BACKGROUND_APPROVAL_TIMEOUT_SECS=180,
        _APPROVAL_TIMEOUT=7200,
        _audit_and_broadcast_approval=lambda *_a, **_k: None,
        _log=logging.getLogger(__name__),
    )


async def _settle() -> None:
    for _ in range(50):
        await asyncio.sleep(0)


@asynccontextmanager
async def _parked_on_dashboard_prompt() -> (
    AsyncIterator[tuple[SubagentManager, SubagentInfo, SimpleNamespace, AsyncMock]]
):
    """A spawn from a dashboard chat, parked on the real dashboard approval wait."""
    state = _dashboard_state()

    async def _approve(request_id: str, description: str, _parent: str = "") -> bool:
        return await ApprovalCoordinator.request(
            state,
            request_id,
            "subagent",
            description,
            tool_input="",
            tool_purpose="",
            slot="first-run",
            is_background=False,
            redact_url=lambda text: (text, None),
            redact_secret=lambda text: (text, None),
        )

    on_done = AsyncMock()
    mgr = SubagentManager(
        sessions=_sessions(),
        ctx_builder=_ctx_builder(),
        on_spawn_approval=_approve,
        on_done=on_done,
        is_yolo=lambda: False,
    )
    try:
        spawned = mgr.spawn(
            "Summarise the imported notes", parent_session_key="dashboard:first-run"
        )
        assert spawned is not None
        await _settle()
        info = mgr._agents[spawned.id]
        assert info._awaiting_approval is True, "precondition: parked on the prompt"
        assert f"spawn:{info.id}" in state._approval_futures
        yield mgr, info, state, on_done
    finally:
        for task in list(mgr._tasks.values()):
            task.cancel()
        await asyncio.sleep(0)
        mgr.close()


class TestAStoppedSpawnIsNotARefusal:
    @pytest.mark.asyncio
    async def test_a_stop_while_the_prompt_is_open_stays_a_neutral_stop(self) -> None:
        async with _parked_on_dashboard_prompt() as (mgr, info, _state, on_done):
            assert await mgr.cancel(info.id) is True
            await _settle()
            assert info.done is True
            assert info.user_stopped is True
            assert info.error == "", "a Stop is neutral; it must not read as a refusal"
            on_done.assert_awaited_once()
            assert on_done.await_args.args[0].error == ""

    @pytest.mark.asyncio
    async def test_a_reap_while_the_prompt_is_open_keeps_the_reap_cause(self) -> None:
        async with _parked_on_dashboard_prompt() as (mgr, info, _state, on_done):
            await mgr._force_reap(info.id, info, 1801.0)
            await _settle()
            assert info.done is True
            assert "spawn rejected" not in info.error
            assert "unanswered spawn approval" in info.error
            on_done.assert_awaited_once()
            assert "unanswered spawn approval" in on_done.await_args.args[0].error

    @pytest.mark.asyncio
    async def test_a_shutdown_while_the_prompt_is_open_records_no_refusal(self) -> None:
        async with _parked_on_dashboard_prompt() as (mgr, info, _state, on_done):
            await mgr.cancel_all()
            await _settle()
            assert "spawn rejected" not in (info.error or "")
            on_done.assert_not_awaited()


class TestARefusalNamesItsGate:
    @pytest.mark.asyncio
    async def test_a_decline_on_the_dashboard_prompt_names_the_gate_and_the_next_step(
        self,
    ) -> None:
        async with _parked_on_dashboard_prompt() as (_mgr, info, state, on_done):
            state._approval_futures[f"spawn:{info.id}"].set_result(False)
            await _settle()
            assert info.done is True
            assert info.error == SPAWN_DECLINED_ERROR
            on_done.assert_awaited_once()
            assert on_done.await_args.args[0].error == SPAWN_DECLINED_ERROR

    def test_the_decline_text_says_what_refused_it_and_what_to_do(self) -> None:
        assert SPAWN_DECLINED_ERROR.startswith("spawn rejected")
        assert "approval prompt" in SPAWN_DECLINED_ERROR
        assert "declined" in SPAWN_DECLINED_ERROR and "expired" in SPAWN_DECLINED_ERROR
        assert "nothing ran" in SPAWN_DECLINED_ERROR
        assert "this conversation" in SPAWN_DECLINED_ERROR
        for recipe in _BYPASS_RECIPES:
            assert recipe not in SPAWN_DECLINED_ERROR

    @pytest.mark.asyncio
    async def test_a_failing_approval_callback_is_reported_as_a_failure(self) -> None:
        mgr = SubagentManager(
            sessions=_sessions(),
            ctx_builder=_ctx_builder(),
            on_spawn_approval=AsyncMock(side_effect=RuntimeError("approval service down")),
            is_yolo=lambda: False,
        )
        try:
            with patch("kiro_crew.subagent.sel") as sel:
                spawned = mgr.spawn(
                    "Summarise the imported notes", parent_session_key="dashboard:x"
                )
                assert spawned is not None
                await _settle()
            info = mgr._agents[spawned.id]
            assert info.done is True
            assert info.error == SPAWN_APPROVAL_FAILED_ERROR
            assert SPAWN_APPROVAL_FAILED_ERROR.startswith("spawn rejected")
            assert "internal error" in SPAWN_APPROVAL_FAILED_ERROR
            for recipe in _BYPASS_RECIPES:
                assert recipe not in SPAWN_APPROVAL_FAILED_ERROR
            rejected = [
                c.kwargs
                for c in sel.return_value.log_tool_invocation.call_args_list
                if c.kwargs.get("outcome") == "rejected"
            ]
            assert rejected and rejected[-1]["metadata"].get("reason") == "approval_error"
        finally:
            mgr.close()
