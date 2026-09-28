"""The first run's guardrails: the stall watchdog, the kickoff notice, the quota pause.

Each guardrail is one deterministic system notice in the first-run chat (RFC
one-chat first run §6.3, §6.7). The stall verdict is the session-health
classifier's, sampled on a shorter window; these tests drive it with a fake
slot whose progress markers do or do not move.
"""

from __future__ import annotations

import asyncio
import weakref
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kiro_crew import first_run
from kiro_crew.dashboard import setup_flow, setup_guardrails
from kiro_crew.dashboard.chat_utils import USAGE_LIMIT_KIND
from kiro_crew.dashboard.system_notices import (
    SETUP_QUOTA_KIND,
    SETUP_STALLED_KIND,
    SYSTEM_NOTICE_KINDS,
)


class FakeSlot:
    def __init__(self, key: str = "chat-1-1") -> None:
        self.key = key
        self.messages: list[dict[str, Any]] = []
        self.task: asyncio.Task[Any] | None = None
        self.queue_depth = 0
        self._approval_futures: dict[str, asyncio.Future[Any]] = {}

    @property
    def turn_running(self) -> bool:
        return self.task is not None and not self.task.done()

    @property
    def running(self) -> bool:
        return self.turn_running

    def append(self, role, content, cls="", ts="", *, broadcast=True, meta=None):
        row: dict[str, Any] = {"role": role, "content": content, "cls": cls, "ts": ts}
        if meta:
            row["meta"] = meta
        self.messages.append(row)
        return row


class FakeState:
    def __init__(self, slot: FakeSlot) -> None:
        self._slots = {slot.key: slot}
        self.subagents = None
        self.pushes = 0

    def get_slot(self, key):
        return self._slots.get(key)

    def push_slots_update(self, **_):
        self.pushes += 1


@pytest.fixture(autouse=True)
def _fresh_guardrails(monkeypatch):
    monkeypatch.setattr(setup_guardrails, "_first_run_slots", weakref.WeakKeyDictionary())
    monkeypatch.setattr(setup_guardrails, "_kickoff_open", set())
    monkeypatch.setattr(setup_guardrails, "_quota_paused", set())
    monkeypatch.setattr(setup_guardrails, "_retrying", set())
    monkeypatch.setattr(setup_guardrails, "_WATCH_POLL_SECS", 0.02)
    monkeypatch.setattr(setup_guardrails, "FIRST_RUN_STALL_SECS", 0.15)


@pytest.fixture
def slot() -> FakeSlot:
    first_run.record_slot("chat-1-1")
    return FakeSlot("chat-1-1")


@pytest.fixture
def state(slot) -> FakeState:
    st = FakeState(slot)
    setup_guardrails.track(st, slot.key)
    return st


async def _turn(state, slot, body=None, *, secs: float = 0.0) -> None:
    """Run one top-level turn the way ``_run_chat`` arms it, then settle."""

    async def _run() -> None:
        setup_guardrails.watch_turn(state, slot)
        if body is not None:
            body(slot)
        await asyncio.sleep(secs)

    slot.task = asyncio.create_task(_run())
    await slot.task
    await asyncio.gather(*list(setup_guardrails._tasks))


def _notices(slot: FakeSlot) -> list[dict[str, Any]]:
    return [
        r["meta"]
        for r in slot.messages
        if r["role"] == "assistant" and r.get("meta", {}).get("kind") in SYSTEM_NOTICE_KINDS
    ]


def _reply(slot: FakeSlot) -> None:
    slot.append("assistant", "Hi! I found a few things.", "msg msg-a")


def _usage_limit(slot: FakeSlot) -> None:
    slot.append(
        "error",
        "The monthly usage limit has been reached. Retrying will not help.",
        "msg msg-err",
        meta={"kind": USAGE_LIMIT_KIND},
    )


def test_the_notice_kinds_are_system_notices():
    assert {SETUP_STALLED_KIND, SETUP_QUOTA_KIND} <= SYSTEM_NOTICE_KINDS


class TestStallWatchdog:
    @pytest.mark.asyncio
    async def test_a_silent_turn_gets_one_notice(self, state, slot):
        await _turn(state, slot, secs=0.6)
        notices = _notices(slot)
        assert notices == [{"kind": SETUP_STALLED_KIND, "reason": "no_output", "secs": 0}]
        text = slot.messages[0]["content"]
        assert "Stop the reply" in text and "/onboarding" in text
        assert state.pushes == 1

    @pytest.mark.asyncio
    async def test_a_turn_waiting_on_an_approval_is_not_stalled(self, state, slot):
        slot._approval_futures["tool-1"] = asyncio.get_running_loop().create_future()
        await _turn(state, slot, secs=0.5)
        assert _notices(slot) == []

    @pytest.mark.asyncio
    async def test_a_turn_that_keeps_producing_is_not_stalled(self, state, slot):
        async def _run() -> None:
            setup_guardrails.watch_turn(state, slot)
            for i in range(12):
                slot.append("chunk", f"part {i}", "chunk")
                await asyncio.sleep(0.05)
            _reply(slot)

        slot.task = asyncio.create_task(_run())
        await slot.task
        await asyncio.gather(*list(setup_guardrails._tasks))
        assert _notices(slot) == []

    @pytest.mark.asyncio
    async def test_other_chats_are_not_watched(self, state):
        other = FakeSlot("chat-2-1")
        state._slots[other.key] = other
        await _turn(state, other, secs=0.5)
        assert _notices(other) == []

    @pytest.mark.asyncio
    async def test_an_untracked_state_starts_no_watch(self, slot):
        from types import SimpleNamespace

        setup_guardrails.watch_turn(SimpleNamespace(_slots={}), slot)
        setup_guardrails.watch_turn(FakeState(slot), slot)
        assert not setup_guardrails._tasks

    @pytest.mark.asyncio
    async def test_the_main_chat_is_not_watched(self, state, slot):
        first_run.record_main(slot.key)
        await _turn(state, slot, secs=0.5)
        assert _notices(slot) == []


class TestKickoff:
    @pytest.mark.asyncio
    async def test_a_kickoff_with_no_reply_gets_the_retry_notice(self, state, slot):
        setup_guardrails.expect_kickoff(state, slot.key)
        await _turn(state, slot)
        assert _notices(slot) == [{"kind": SETUP_STALLED_KIND, "reason": "kickoff_failed"}]
        assert "Try again" in slot.messages[-1]["content"]
        # Posted once: a later silent turn is not a kickoff failure.
        await _turn(state, slot)
        assert len(_notices(slot)) == 1

    @pytest.mark.asyncio
    async def test_an_answered_kickoff_posts_nothing(self, state, slot):
        setup_guardrails.expect_kickoff(state, slot.key)
        await _turn(state, slot, _reply)
        assert _notices(slot) == []
        await _turn(state, slot)
        assert _notices(slot) == []

    @pytest.mark.asyncio
    async def test_a_kickoff_a_retry_picks_up_is_judged_by_the_retry(self, state, slot):
        setup_guardrails.expect_kickoff(state, slot.key)
        slot.queue_depth = 1
        await _turn(state, slot)
        assert _notices(slot) == []
        slot.queue_depth = 0
        await _turn(state, slot, _reply)
        assert _notices(slot) == []

    @pytest.mark.asyncio
    async def test_a_kickoff_that_cannot_be_dispatched_says_so(self, state, slot, monkeypatch):
        async def _boom(*_a, **_k):
            raise RuntimeError("no runtime")

        monkeypatch.setattr(setup_flow, "_dispatch_envelope_turn", _boom)
        monkeypatch.setattr(setup_flow, "_kickoff_facts", lambda: [])
        await setup_flow.start_first_run_turn(state, slot)
        assert _notices(slot) == [{"kind": SETUP_STALLED_KIND, "reason": "kickoff_failed"}]


class TestQuota:
    @pytest.mark.asyncio
    async def test_a_spent_allowance_gets_one_notice_and_pauses_cards(self, state, slot):
        await _turn(state, slot, _usage_limit)
        assert _notices(slot) == [{"kind": SETUP_QUOTA_KIND}]
        text = slot.messages[-1]["content"]
        assert "allowance has run out" in text and "/onboarding" in text
        assert setup_guardrails.quota_paused(slot.key)
        # The next failing turn is the same episode: no second notice.
        await _turn(state, slot, _usage_limit)
        assert len(_notices(slot)) == 1

    @pytest.mark.asyncio
    async def test_the_quota_notice_replaces_the_kickoff_notice(self, state, slot):
        setup_guardrails.expect_kickoff(state, slot.key)
        await _turn(state, slot, _usage_limit)
        assert _notices(slot) == [{"kind": SETUP_QUOTA_KIND}]

    @pytest.mark.asyncio
    async def test_a_landed_turn_lifts_the_pause(self, state, slot):
        await _turn(state, slot, _usage_limit)
        await _turn(state, slot, _reply)
        assert not setup_guardrails.quota_paused(slot.key)
        # A new episode is reported again.
        await _turn(state, slot, _usage_limit)
        assert len(_notices(slot)) == 2

    @pytest.mark.asyncio
    async def test_a_reply_after_the_limit_is_not_an_episode(self, state, slot):
        def _fell_back(s):
            _usage_limit(s)
            _reply(s)

        await _turn(state, slot, _fell_back)
        assert _notices(slot) == []
        assert not setup_guardrails.quota_paused(slot.key)

    @pytest.mark.asyncio
    async def test_propose_refuses_while_paused(self, state, slot, monkeypatch):
        monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: None)
        await _turn(state, slot, _usage_limit)
        out = await setup_flow.propose(
            state,
            slot,
            f"dashboard:{slot.key}",
            {"kind": "profile", "fields": {"bot_name": "Nova"}},
            producer_is_user_facing=True,
        )
        assert out.startswith("Error:") and "allowance" in out
        from kiro_crew import setup_cards as sc

        assert sc.list_cards(slot.key) == []


class TestRetry:
    @pytest.fixture
    def acked(self, monkeypatch):
        cfg = MagicMock()
        cfg.dashboard.privacy_acked = True
        monkeypatch.setattr("kiro_crew.config.loader.KiroCrewConfig.load", lambda: cfg)
        return cfg

    @pytest.fixture
    def dispatched(self, monkeypatch):
        calls: list[tuple[str, str]] = []

        async def _fake(state, slot, text, inject_kind):
            calls.append((slot.key, inject_kind))

        monkeypatch.setattr(setup_flow, "_dispatch_envelope_turn", _fake)
        monkeypatch.setattr(setup_flow, "_kickoff_facts", lambda: [])
        return calls

    @pytest.mark.asyncio
    async def test_retry_sends_the_kickoff_again(self, state, slot, acked, dispatched):
        slot.append("inject", "[First run] ...", "msg msg-inject", meta={"injectKind": "first_run"})
        assert await setup_guardrails.retry_kickoff(state) == slot.key
        assert dispatched == [(slot.key, "first_run")]
        assert slot.key in setup_guardrails._kickoff_open

    @pytest.mark.asyncio
    async def test_retry_is_refused_once_the_kickoff_was_answered(
        self, state, slot, acked, dispatched
    ):
        from kiro_crew import setup_cards as sc

        slot.append("inject", "[First run] ...", "msg msg-inject", meta={"injectKind": "first_run"})
        _reply(slot)
        with pytest.raises(sc.CardRejected) as exc:
            await setup_guardrails.retry_kickoff(state)
        assert exc.value.code == "kickoff_answered"
        assert dispatched == []

    @pytest.mark.asyncio
    async def test_retry_is_refused_while_a_turn_runs(self, state, slot, acked, dispatched):
        from kiro_crew import setup_cards as sc

        slot.task = asyncio.create_task(asyncio.sleep(10))
        try:
            with pytest.raises(sc.CardRejected) as exc:
                await setup_guardrails.retry_kickoff(state)
        finally:
            slot.task.cancel()
        assert exc.value.code == "turn_running"
        assert dispatched == []

    @pytest.mark.asyncio
    async def test_retry_is_refused_before_the_privacy_card(self, state, slot, acked, dispatched):
        from kiro_crew import setup_cards as sc

        acked.dashboard.privacy_acked = False
        with pytest.raises(sc.CardRejected) as exc:
            await setup_guardrails.retry_kickoff(state)
        assert exc.value.code == "privacy_not_acked"
        assert dispatched == []


class TestRunnerHook:
    """``_run_chat`` arms the watch at depth 0, so a real turn's rows are judged."""

    @pytest.mark.asyncio
    async def test_a_spent_allowance_in_a_real_turn_posts_the_quota_notice(self, tmp_path):
        from chat_test_helpers import _make_state

        from kiro_crew.acp.client import AcpError
        from kiro_crew.dashboard.chat_runner import _run_chat

        st = _make_state(tmp_path)
        st.sessions.release = MagicMock()
        st.sessions.reset = AsyncMock()
        st.sessions.set_approval_policy = MagicMock()
        st.sessions.check_context_usage = MagicMock()
        st.sessions.get_slack_link = MagicMock(return_value=(None, None))
        st.broadcast_ws = MagicMock()
        st.push_slots_update = MagicMock()
        st.is_yolo_active = MagicMock(return_value=False)
        st._background_tasks = set()
        real = st.get_or_create_slot("chat-9-1")
        first_run.record_slot(real.key)
        setup_guardrails.track(st, real.key)

        client = MagicMock()
        client.shutdown = AsyncMock()

        async def _stream(msg):
            err = AcpError("The monthly usage limit has been reached.", transient=False)
            err.usage_limit = True
            raise err
            yield  # pragma: no cover - makes this an async generator

        client.stream = _stream
        client.stream_command = _stream
        st.sessions.get_or_create = AsyncMock(return_value=(client, True, False))
        real.append("user", "hello", "msg msg-u")

        real.task = asyncio.create_task(_run_chat(st, real, "hello"))
        await real.task
        await asyncio.wait_for(asyncio.gather(*list(setup_guardrails._tasks)), 5)

        errors = [m for m in real.messages if m.get("role") == "error"]
        assert errors and errors[-1].get("meta", {}).get("kind") == USAGE_LIMIT_KIND
        kinds = [m.get("meta", {}).get("kind") for m in real.messages if m["role"] == "assistant"]
        assert SETUP_QUOTA_KIND in kinds
        assert setup_guardrails.quota_paused(real.key)


class TestRetryRoute:
    def _app(self, state):
        from aiohttp import web

        from kiro_crew.dashboard.handlers import setup_cards as handlers

        app = web.Application()
        app["state"] = state
        handlers.register_routes(app)
        return app

    @pytest.mark.asyncio
    async def test_a_stranger_cannot_retry(self, state, monkeypatch):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        from kiro_crew.dashboard.handlers import setup_cards as handlers

        async def _gate(request, operation):
            return web.json_response({"error": "owner only", "code": "owner_only"}, status=403)

        monkeypatch.setattr(handlers, "require_owner_dashboard_request", _gate)
        async with TestClient(TestServer(self._app(state))) as client:
            r = await client.post("/api/setup/first-run/retry")
            assert r.status == 403

    @pytest.mark.asyncio
    async def test_the_owner_gets_a_coded_refusal(self, state, slot, monkeypatch):
        from aiohttp.test_utils import TestClient, TestServer

        from kiro_crew.dashboard.handlers import setup_cards as handlers

        async def _gate(request, operation):
            return None

        monkeypatch.setattr(handlers, "require_owner_dashboard_request", _gate)
        slot.append("inject", "[First run] ...", "msg msg-inject", meta={"injectKind": "first_run"})
        _reply(slot)
        cfg = MagicMock()
        cfg.dashboard.privacy_acked = True
        monkeypatch.setattr("kiro_crew.config.loader.KiroCrewConfig.load", lambda: cfg)
        async with TestClient(TestServer(self._app(state))) as client:
            r = await client.post("/api/setup/first-run/retry")
            assert r.status == 409
            assert (await r.json())["code"] == "kickoff_answered"
