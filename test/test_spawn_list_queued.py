"""``spawn_list`` / ``GET /api/spawn`` report the caller's spawns that are WAITING.

A spawn accepted behind the concurrency cap, the stagger tick or the memory gate
sits in the manager's queue (or the task store's overflow) with no
``SubagentInfo`` yet, so the run list could not show it. The wave chip read
``running 0 · queued 1 · done 3`` while ``spawn_list`` listed only the three
finished runs, and the agent could not tell a real wait from a stale chip.

The route now adds ``caller_queue`` -- the caller's OWN waiting depth plus the
gate's label for why -- keyed on the caller's session header alone, and the
tool prints ``N waiting to start — <why>`` above the run rows.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from aiohttp import web

from kiro_crew.dashboard.handlers import messaging as mod
from kiro_crew.mcp_tools import spawn as spawn_tools

_LOW_MEMORY = {"reason": "low_memory", "available_gb": 4.2, "required_gb": 4.5}


def _manager() -> Any:
    from kiro_crew.subagent import SubagentManager

    sessions = MagicMock()
    sessions.get_agent_selection.return_value = ("template", "")
    return SubagentManager(
        sessions=sessions, ctx_builder=MagicMock(), on_done=MagicMock(), max_concurrent=2
    )


class _Req:
    """Request double carrying the headers and flags ``api_spawn_list`` reads."""

    def __init__(self, state: Any, *, session: str = "", internal: bool = False) -> None:
        self.app = {"state": state}
        self.headers = {"X-Session-Key": session} if session else {}
        self.query: dict[str, str] = {}
        self._extra = {"internal_auth": True} if internal else {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._extra.get(key, default)


def _list(mgr: Any, **kw: Any) -> dict[str, Any]:
    state = SimpleNamespace(subagents=mgr)

    async def _unscoped(request: Any, operation: str, **_: Any) -> tuple[None, None]:
        return None, None

    with patch.object(mod, "internal_memory_scope", _unscoped):
        resp = asyncio.run(mod.api_spawn_list(_Req(state, **kw)))
    assert isinstance(resp, web.Response) and resp.status == 200
    body = resp.body
    assert isinstance(body, (bytes, bytearray))
    return json.loads(body)


def _queue(mgr: Any, parent: str, n: int, wait: dict[str, Any] | None = None) -> None:
    for i in range(n):
        mgr._queue.append({"id": f"{parent}-q{i}", "task": "t", "parent_session_key": parent})
    if wait is not None:
        mgr._queue_wait[parent] = dict(wait)


class TestApiSpawnListReportsTheCallersQueue:
    def test_caller_gets_its_depth_and_the_gate_label(self) -> None:
        mgr = _manager()
        _queue(mgr, "dashboard:chat-1", 1, _LOW_MEMORY)
        body = _list(mgr, session="dashboard:chat-1", internal=True)
        assert body["agents"] == []
        assert body["caller_queue"] == {"queued": 1, **_LOW_MEMORY}

    def test_another_sessions_depth_and_label_are_not_reported(self) -> None:
        mgr = _manager()
        _queue(mgr, "dashboard:chat-1", 1)
        _queue(mgr, "dashboard:other", 3, _LOW_MEMORY)
        body = _list(mgr, session="dashboard:chat-1", internal=True)
        # Only the caller's own row is counted; the other session's depth and
        # its memory label leave no trace in the answer.
        assert body["caller_queue"] == {"queued": 1}
        assert "other" not in json.dumps(body)

    def test_a_label_left_at_depth_zero_is_not_reported(self) -> None:
        mgr = _manager()
        mgr._queue_wait["dashboard:chat-1"] = dict(_LOW_MEMORY)
        body = _list(mgr, session="dashboard:chat-1", internal=True)
        assert body["caller_queue"] == {"queued": 0}

    def test_no_session_key_leaves_the_payload_unchanged(self) -> None:
        mgr = _manager()
        _queue(mgr, "dashboard:chat-1", 2, _LOW_MEMORY)
        assert _list(mgr) == {"agents": []}

    def test_queue_wait_for_returns_a_copy(self) -> None:
        mgr = _manager()
        mgr._queue_wait["p"] = dict(_LOW_MEMORY)
        got = mgr.queue_wait_for("p")
        got["reason"] = "mutated"
        assert mgr._queue_wait["p"]["reason"] == "low_memory"
        assert mgr.queue_wait_for("missing") == {}


def _render(answer: dict[str, Any]) -> str:
    with (
        patch.object(spawn_tools.mcp_core, "_get", return_value=answer),
        patch.object(spawn_tools.mcp_core, "list_agents", return_value=[]),
    ):
        return spawn_tools.spawn_list("spawn_list", {})


_DONE = {"id": "r1", "task": "finished task", "done": True, "result": "", "error": ""}


class TestSpawnListRendersWaitingRows:
    def test_queued_rows_are_reported_with_the_memory_figures(self) -> None:
        out = _render({"agents": [_DONE], "caller_queue": {"queued": 1, **_LOW_MEMORY}})
        lines = out.splitlines()
        assert lines[0] == (
            "1 waiting to start — needs 4.5GB of free memory, 4.2GB free now; "
            "free up memory to continue"
        )
        assert lines[1].startswith("r1  [done]")

    def test_only_queued_rows_do_not_say_nothing_is_running(self) -> None:
        out = _render({"agents": [], "caller_queue": {"queued": 2}})
        assert out.splitlines()[0] == "2 waiting to start — queued behind the concurrency limit"
        assert "No subagents running." not in out

    def test_each_reason_has_its_own_text(self) -> None:
        cases = {
            "posture_critical": "memory is critically low (1.5GB free)",
            "adaptive_cap_zero": "starts are paused",
            "concurrency_limit": "queued behind the concurrency limit",
        }
        for reason, text in cases.items():
            q = {"queued": 1, "reason": reason, "available_gb": 1.5}
            assert text in _render({"agents": [], "caller_queue": q}), reason

    def test_memory_reason_without_figures_uses_the_figureless_text(self) -> None:
        out = _render({"agents": [], "caller_queue": {"queued": 1, "reason": "low_memory"}})
        assert "not enough free memory" in out and "None" not in out

    def test_zero_depth_or_older_gateway_prints_nothing_new(self) -> None:
        for answer in ({"agents": []}, {"agents": [], "caller_queue": {"queued": 0}}):
            out = _render(answer)
            assert out.splitlines()[0] == "No subagents running."
            assert "waiting to start" not in out
        out = _render({"agents": [_DONE], "caller_queue": {"queued": 0}})
        assert "waiting to start" not in out
