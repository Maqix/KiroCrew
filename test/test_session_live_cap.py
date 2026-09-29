"""The live chat cap (``session.max_live_sessions``).

Each live chat is a kiro-cli child plus its MCP servers (~0.39 GB on a small
home), and nothing but the idle timeout bounded how many stayed up, so a 2 GB
home ran out of memory with a handful of chats open. The cap is sized from host
memory unless pinned, and opening a chat past it first releases the least
recently used idle chat, which resumes on its next message. It never releases a
chat with a turn in flight, one the user is holding open (the main chat, a
question card), sub-agent work's parent, or the background runtime, and it never
refuses the new chat.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kiro_crew import platform_compat
from kiro_crew.config import KiroCrewConfig, config_path
from kiro_crew.session import BACKGROUND_KEY, SessionManager
from kiro_crew.session_live_cap import (
    LIVE_SESSIONS_AUTO_CEILING,
    LIVE_SESSIONS_AUTO_FLOOR,
    auto_max_live_sessions,
    resolve_max_live_sessions,
)

# Every await on the manager is bounded: a regression that parks one must fail
# this test, not take the worker down with it.
_BOUND_SECS = 10.0


def _bounded(awaitable):
    return asyncio.wait_for(awaitable, timeout=_BOUND_SECS)


def _factory():
    def factory(session_key=None, agent=None, channel_id=None, **kwargs):
        provider = AsyncMock()
        provider.start = AsyncMock()
        provider.shutdown = AsyncMock()
        provider.context_usage_pct = lambda: 0.0
        provider.has_active_turn = lambda: False
        provider.is_process_alive = MagicMock(return_value=True)
        return provider

    return factory


def _manager(cap: int) -> SessionManager:
    cfg = KiroCrewConfig()
    cfg.session.max_live_sessions = cap
    return SessionManager(cfg, provider_factory=_factory())


async def _open_idle(mgr: SessionManager, *keys: str) -> None:
    """Open each chat and end its turn; listed oldest first by `last_used`."""
    for age, key in enumerate(reversed(keys), start=1):
        await _bounded(mgr.get_or_create(key))
        mgr.release(key)
        async with mgr._lock:
            mgr._sessions[key].last_used = time.monotonic() - 100 * age


class TestTheCapMath:
    """2 on a 2 GB home, 5 on 4 GB, about 35 on 16 GB, 64 at most."""

    @pytest.mark.parametrize(
        ("total_mib", "cap"),
        [
            (1000, LIVE_SESSIONS_AUTO_FLOOR),  # smaller than the reserve itself
            (1890, 2),  # t4g.small, 2 GiB
            (3850, 5),  # t4g.medium, 4 GiB
            (7800, 15),  # 8 GiB
            (15872, 35),  # a 16 GB laptop
            (64000, LIVE_SESSIONS_AUTO_CEILING),
        ],
    )
    def test_the_auto_cap_follows_total_memory(self, total_mib, cap):
        assert auto_max_live_sessions(total_mib) == cap

    def test_an_unreadable_host_keeps_the_uncapped_behaviour(self):
        assert auto_max_live_sessions(0) == LIVE_SESSIONS_AUTO_CEILING
        assert auto_max_live_sessions(-1) == LIVE_SESSIONS_AUTO_CEILING

    def test_a_normal_laptop_is_not_held_to_a_small_home_cap(self):
        assert auto_max_live_sessions(16 * 1024) > 30

    def test_zero_is_auto_from_host_memory(self, monkeypatch):
        monkeypatch.setattr(platform_compat, "host_total_mib", lambda: 1890)
        assert resolve_max_live_sessions(0) == 2

    def test_an_explicit_value_overrides_auto_both_ways(self, monkeypatch):
        monkeypatch.setattr(platform_compat, "host_total_mib", lambda: 64000)
        assert resolve_max_live_sessions(3) == 3
        monkeypatch.setattr(platform_compat, "host_total_mib", lambda: 1890)
        assert resolve_max_live_sessions(200) == 200

    @pytest.mark.parametrize("configured", [True, -4, "7", None, MagicMock()])
    def test_anything_but_a_positive_int_is_auto(self, monkeypatch, configured):
        monkeypatch.setattr(platform_compat, "host_total_mib", lambda: 1890)
        assert resolve_max_live_sessions(configured) == 2


class TestTheConfigKey:
    def test_the_default_is_auto(self):
        assert KiroCrewConfig().session.max_live_sessions == 0

    @pytest.mark.parametrize(
        ("on_disk", "loaded"),
        [(6, 6), (0, 0), (-3, 0), (99999, 512), ("abc", 0), (True, 0)],
    )
    def test_it_loads_clamped(self, on_disk, loaded):
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"session": {"max_live_sessions": on_disk}}), encoding="utf-8")
        assert KiroCrewConfig.load().session.max_live_sessions == loaded

    def test_it_applies_without_a_restart(self):
        from kiro_crew.config.schema import requires_restart

        assert not requires_restart("session.max_live_sessions")


class TestLeastRecentlyUsedIdleChatGoesFirst:
    @pytest.mark.asyncio
    async def test_the_oldest_idle_chat_is_released_for_a_new_one(self):
        mgr = _manager(cap=2)
        await _open_idle(mgr, "dashboard:old", "dashboard:recent")

        await _bounded(mgr.get_or_create("dashboard:new"))

        assert set(mgr._sessions) == {"dashboard:recent", "dashboard:new"}
        mgr.release("dashboard:new")
        await _bounded(mgr.close_all())

    @pytest.mark.asyncio
    async def test_as_many_go_as_it_takes_to_fit_a_lowered_cap(self):
        mgr = _manager(cap=4)
        await _open_idle(mgr, "slack:a", "slack:b", "slack:c")
        # Read at the point of use: a lowered cap governs the next open.
        mgr._cfg.session.max_live_sessions = 2

        await _bounded(mgr.get_or_create("slack:d"))

        assert set(mgr._sessions) == {"slack:c", "slack:d"}
        mgr.release("slack:d")
        await _bounded(mgr.close_all())

    @pytest.mark.asyncio
    async def test_under_the_cap_nothing_is_released(self):
        mgr = _manager(cap=3)
        await _open_idle(mgr, "dashboard:a", "dashboard:b")

        await _bounded(mgr.get_or_create("dashboard:c"))

        assert mgr.count == 3
        mgr.release("dashboard:c")
        await _bounded(mgr.close_all())

    @pytest.mark.asyncio
    async def test_reusing_a_live_chat_releases_nothing(self):
        mgr = _manager(cap=2)
        await _open_idle(mgr, "dashboard:a", "dashboard:b")

        await _bounded(mgr.get_or_create("dashboard:a"))

        assert set(mgr._sessions) == {"dashboard:a", "dashboard:b"}
        mgr.release("dashboard:a")
        await _bounded(mgr.close_all())

    @pytest.mark.asyncio
    async def test_the_release_is_the_idle_expiry_one_so_the_chat_resumes(self):
        """Same reset as ``_expire_idle``: the conversation entry is kept.

        No ``clear_conversation`` and no ``ends_conversation``, so the session map
        keeps the resume id and the chat comes back through ``session/load``; and
        the consolidation hook runs as it does at expiry.
        """
        mgr = _manager(cap=2)
        await _open_idle(mgr, "dashboard:old", "dashboard:recent")
        scanned = mgr._sessions["dashboard:old"]
        expired: list[str] = []
        mgr.on_session_expire = expired.append
        real_reset = mgr.reset
        mgr.reset = AsyncMock(side_effect=real_reset)  # type: ignore[method-assign]

        await _bounded(mgr.get_or_create("dashboard:new"))

        mgr.reset.assert_awaited_once()
        args, kwargs = mgr.reset.await_args
        assert args == ("dashboard:old",)
        assert kwargs == {
            "expect_session": scanned,
            "skip_if_busy": True,
            "skip_if_injecting": True,
        }
        assert expired == ["dashboard:old"]
        mgr.release("dashboard:new")
        await _bounded(mgr.close_all())


class TestSpeculativeSpawns:
    """An eager spawn counts like any open, unless the allocation will refuse it."""

    @pytest.mark.asyncio
    async def test_an_eager_spawn_makes_room(self):
        mgr = _manager(cap=2)
        await _open_idle(mgr, "dashboard:old", "dashboard:recent")

        await _bounded(mgr.get_or_create("dashboard:new", speculative=True))

        assert set(mgr._sessions) == {"dashboard:recent", "dashboard:new"}
        mgr.release("dashboard:new")
        await _bounded(mgr.close_all())

    @pytest.mark.asyncio
    async def test_a_spawn_refused_for_its_resume_releases_nothing(self, monkeypatch):
        mgr = _manager(cap=2)
        await _open_idle(mgr, "dashboard:old", "dashboard:recent")
        monkeypatch.setattr(mgr._session_map, "has_hint", lambda key: key == "dashboard:new")
        make_room = AsyncMock(return_value=0)
        monkeypatch.setattr(mgr._cleanup_boundary(), "make_room", make_room)

        await _bounded(mgr.get_or_create("dashboard:new", speculative=True))

        make_room.assert_not_awaited()
        mgr.release("dashboard:new")
        await _bounded(mgr.close_all())


class TestWhatIsNeverReleased:
    @pytest.mark.asyncio
    async def test_a_chat_with_a_turn_in_flight_is_skipped(self):
        mgr = _manager(cap=2)
        await _bounded(mgr.get_or_create("dashboard:busy"))  # permit held
        async with mgr._lock:
            mgr._sessions["dashboard:busy"].last_used = time.monotonic() - 1000
        await _open_idle(mgr, "dashboard:idle")

        await _bounded(mgr.get_or_create("dashboard:new"))

        assert set(mgr._sessions) == {"dashboard:busy", "dashboard:new"}
        for key in ("dashboard:busy", "dashboard:new"):
            mgr.release(key)
        await _bounded(mgr.close_all())

    @pytest.mark.asyncio
    @pytest.mark.parametrize("awaitable", [False, True])
    async def test_a_chat_the_user_holds_open_is_skipped(self, awaitable):
        """The main chat, or one with a question card waiting: the dashboard's probe."""
        mgr = _manager(cap=2)
        await _open_idle(mgr, "dashboard:main", "dashboard:other")
        if awaitable:

            async def probe(key):
                return key == "dashboard:main"

        else:

            def probe(key):
                return key == "dashboard:main"

        mgr.set_keep_live_probe(probe)

        await _bounded(mgr.get_or_create("dashboard:new"))

        assert set(mgr._sessions) == {"dashboard:main", "dashboard:new"}
        mgr.release("dashboard:new")
        await _bounded(mgr.close_all())

    @pytest.mark.asyncio
    async def test_a_probe_that_cannot_answer_keeps_the_chat(self):
        mgr = _manager(cap=2)
        await _open_idle(mgr, "dashboard:a")

        def probe(key):
            raise RuntimeError("state unavailable")

        mgr.set_keep_live_probe(probe)

        await _bounded(mgr.get_or_create("dashboard:new"))

        assert set(mgr._sessions) == {"dashboard:a", "dashboard:new"}
        mgr.release("dashboard:new")
        await _bounded(mgr.close_all())

    @pytest.mark.asyncio
    async def test_a_parent_with_sub_agent_work_is_skipped(self):
        mgr = _manager(cap=2)
        await _open_idle(mgr, "dashboard:parent", "dashboard:plain")
        mgr.set_subagent_probe(lambda key: key == "dashboard:parent")

        await _bounded(mgr.get_or_create("dashboard:new"))

        assert set(mgr._sessions) == {"dashboard:parent", "dashboard:new"}
        mgr.release("dashboard:new")
        await _bounded(mgr.close_all())

    @pytest.mark.asyncio
    async def test_the_background_runtime_and_automation_are_neither_counted_nor_released(
        self,
    ):
        mgr = _manager(cap=2)
        await _open_idle(mgr, BACKGROUND_KEY, "subagent:run1", "cron:job", "dashboard:a")

        await _bounded(mgr.get_or_create("dashboard:b"))

        # Only one chat was live, so the second one fit without a release.
        assert set(mgr._sessions) == {
            BACKGROUND_KEY,
            "subagent:run1",
            "cron:job",
            "dashboard:a",
            "dashboard:b",
        }
        mgr.release("dashboard:b")
        await _bounded(mgr.close_all())

    @pytest.mark.asyncio
    async def test_opening_automation_releases_no_chat(self):
        mgr = _manager(cap=1)
        await _open_idle(mgr, "dashboard:a")

        await _bounded(mgr.get_or_create("taskrunner:t1:step1"))

        assert "dashboard:a" in mgr._sessions
        mgr.release("taskrunner:t1:step1")
        await _bounded(mgr.close_all())


class TestNothingReleasableStillOpens:
    @pytest.mark.asyncio
    async def test_the_new_chat_opens_and_the_shortfall_is_logged(self, caplog):
        mgr = _manager(cap=1)
        await _bounded(mgr.get_or_create("dashboard:busy"))  # permit held

        with caplog.at_level(logging.WARNING, logger="kiro_crew.session"):
            await _bounded(mgr.get_or_create("dashboard:new"))

        assert set(mgr._sessions) == {"dashboard:busy", "dashboard:new"}
        assert any("opening dashboard:new anyway" in r.getMessage() for r in caplog.records)
        for key in ("dashboard:busy", "dashboard:new"):
            mgr.release(key)
        await _bounded(mgr.close_all())

    @pytest.mark.asyncio
    async def test_a_failure_to_make_room_never_refuses_the_chat(self, monkeypatch):
        mgr = _manager(cap=1)
        await _open_idle(mgr, "dashboard:a")

        async def broken(incoming, cap):
            raise RuntimeError("make_room broke")

        monkeypatch.setattr(mgr._cleanup_boundary(), "make_room", broken)

        provider, _, _ = await _bounded(mgr.get_or_create("dashboard:new"))

        assert provider is not None
        assert "dashboard:new" in mgr._sessions
        mgr.release("dashboard:new")
        await _bounded(mgr.close_all())

    @pytest.mark.asyncio
    async def test_a_start_in_flight_is_counted(self):
        """A cold start that has not registered yet will hold a process too."""
        mgr = _manager(cap=2)
        await _open_idle(mgr, "dashboard:a")
        mgr._registry_state().allocation_reservations["dashboard:starting"] = {object()}

        await _bounded(mgr.get_or_create("dashboard:new"))

        assert "dashboard:a" not in mgr._sessions
        mgr._registry_state().allocation_reservations.pop("dashboard:starting")
        mgr.release("dashboard:new")
        await _bounded(mgr.close_all())


class TestTheDashboardKeepLiveProbe:
    """``wire_session_keep_live_probe``: main chat and waiting chats keep their process."""

    def _probe(self, monkeypatch, *, main, slots):
        from kiro_crew.dashboard import chat_utils

        monkeypatch.setattr("kiro_crew.first_run.read_main_slot", lambda: main)
        monkeypatch.setattr(
            chat_utils, "dashboard_slot_key", lambda key: key.removeprefix("dashboard:")
        )
        monkeypatch.setattr(chat_utils, "effective_session_key", lambda slot: slot.session_key)
        state = SimpleNamespace(
            get_slot=slots.get,
            pending_coordinator_approvals=lambda slot_key: slots[slot_key].approvals,
            sessions=MagicMock(),
        )
        chat_utils.wire_session_keep_live_probe(state)  # type: ignore[arg-type]
        return state.sessions.set_keep_live_probe.call_args.args[0]

    @staticmethod
    def _slot(key, *, question=False, approvals=()):
        return SimpleNamespace(
            key=key,
            session_key=f"dashboard:{key}",
            _question_pending={"card": {}} if question else {},
            approvals=list(approvals),
        )

    @pytest.mark.asyncio
    async def test_the_main_chat_is_held(self, monkeypatch):
        slots = {"main": self._slot("main"), "other": self._slot("other")}
        probe = self._probe(monkeypatch, main="main", slots=slots)
        assert await _bounded(probe("dashboard:main")) is True
        assert await _bounded(probe("dashboard:other")) is False

    @pytest.mark.asyncio
    async def test_a_chat_waiting_on_the_user_is_held(self, monkeypatch):
        slots = {
            "asked": self._slot("asked", question=True),
            "approving": self._slot("approving", approvals=[{"id": "x"}]),
            "done": self._slot("done"),
        }
        probe = self._probe(monkeypatch, main=None, slots=slots)
        assert await _bounded(probe("dashboard:asked")) is True
        assert await _bounded(probe("dashboard:approving")) is True
        assert await _bounded(probe("dashboard:done")) is False

    def test_both_boot_paths_install_it(self):
        """start_dashboard AND start_api_server wire the probe once the state exists."""
        import inspect

        from kiro_crew.dashboard import server

        for fn in (server.start_dashboard, server.start_api_server):
            src = inspect.getsource(fn)
            assert "wire_session_keep_live_probe(state)" in src, fn.__name__
            assert src.index("state = DashboardState(") < src.index(
                "wire_session_keep_live_probe(state)"
            ), fn.__name__
