"""A home build a gateway restart interrupted (``setup_flow.resume_home_builds``).

The build's worker is a daemon thread, so after a restart its card still says
``waiting`` and nothing watches it. At boot the launch store's reap settles every
job no worker here drives, and each waiting home card gets its watcher back,
which follows a live build or settles the card from the job's real outcome.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from kiro_crew import setup_cards as sc
from kiro_crew.cloud import launch_job as lj
from kiro_crew.dashboard import setup_flow


class _State:
    def __init__(self, root: Path) -> None:
        self.events: list = []
        self.cloud_launch_store = lj.LaunchJobStore(root=root)

    def get_slot(self, key):
        return None

    def broadcast_ws_owners(self, msg_type, data):
        self.events.append((msg_type, data))


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    """The job store's directory, a store that wrote there before the restart, and fast polls."""
    monkeypatch.setattr(setup_flow, "_CONNECT_POLL_SECS", 0)
    root = tmp_path / "jobs"
    before = lj.LaunchJobStore(root=root)
    return root, before


def _job(store: lj.LaunchJobStore, **fields: Any) -> lj.LaunchJob:
    job = store.create(profile="", region="us-east-1", size_key="lite")
    for key, value in fields.items():
        setattr(job, key, value)
    store.save(job)
    return job


def _waiting_card(job_id: str, *, phase: str = "build") -> sc.SetupCard:
    card = sc.create_card(
        slot="chat-1-1",
        session_key="dashboard:chat-1-1",
        kind=sc.KIND_HOME,
        payload={"simulated": False, "region": "us-east-1"},
        private={
            "settings": {"region": "us-east-1", "profile": "", "size": "lite"},
            "phase": phase,
            "job_id": job_id,
        },
    )

    def _waiting(c: sc.SetupCard) -> None:
        c.status = sc.STATUS_WAITING

    return sc.update_card(card.id, _waiting)


async def _settled(card_id: str, *, timeout: float = 5.0) -> sc.SetupCard:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        card = sc.get_card(card_id)
        assert card is not None
        if card.status != sc.STATUS_WAITING:
            return card
        assert asyncio.get_running_loop().time() < deadline, "the card was never settled"
        await asyncio.sleep(0.01)


async def _drain() -> None:
    for task in list(setup_flow._home_watchers.values()):
        task.cancel()
    await asyncio.gather(*setup_flow._home_watchers.values(), return_exceptions=True)


class TestSettledFromTheJob:
    @pytest.mark.asyncio
    async def test_a_build_the_restart_cut_short_fails_with_its_real_reason(self, jobs):
        root, before = jobs
        job = _job(before, status=lj.RUNNING)
        card = _waiting_card(job.id)
        state = _State(root)
        assert await setup_flow.resume_home_builds(state) == 1
        out = await _settled(card.id)
        assert out.status == sc.STATUS_FAILED
        assert out.error is not None and out.error["code"] == "home_build_failed"
        assert "Kiro Crew restarted" in out.error["message"]
        assert state.cloud_launch_reaped is True

    @pytest.mark.asyncio
    async def test_a_build_done_and_signed_in_is_ready_to_move_in(self, jobs):
        root, before = jobs
        job = _job(before, status=lj.DONE, signin_detected=True, instance_id="i-0abc")
        card = _waiting_card(job.id)
        await setup_flow.resume_home_builds(_State(root))
        out = await _settled(card.id)
        assert out.status == sc.STATUS_PENDING
        assert out.outcome is not None and out.outcome["ready"] is True
        assert out.private["phase"] == "move" and out.private["instance_id"] == "i-0abc"

    @pytest.mark.asyncio
    async def test_a_build_cut_short_in_its_sign_in_needs_the_sign_in(self, jobs):
        root, before = jobs
        job = before.create(profile="", region="us-east-1", size_key="lite")
        job.status = lj.RUNNING
        job.step(lj.STEP_CONNECT).state = lj.STEP_DONE
        job.step(lj.STEP_SIGNIN).state = lj.STEP_ACTIVE
        before.save(job)
        card = _waiting_card(job.id)
        await setup_flow.resume_home_builds(_State(root))
        out = await _settled(card.id)
        assert out.status == sc.STATUS_PENDING
        assert out.outcome is not None
        assert out.outcome["ready"] is False and out.outcome["needs_signin"] is True
        assert out.private["phase"] == "signin"

    @pytest.mark.asyncio
    async def test_a_missing_job_is_untracked(self, jobs):
        root, _before = jobs
        card = _waiting_card("0123456789ab")
        await setup_flow.resume_home_builds(_State(root))
        out = await _settled(card.id)
        assert out.status == sc.STATUS_FAILED
        assert out.error is not None and out.error["code"] == "home_build_untracked"

    @pytest.mark.asyncio
    async def test_an_unreadable_job_is_untracked(self, jobs):
        root, before = jobs
        job = _job(before, status=lj.RUNNING)
        (root / f"{job.id}.json").write_text("{not json", encoding="utf-8")
        card = _waiting_card(job.id)
        await setup_flow.resume_home_builds(_State(root))
        out = await _settled(card.id)
        assert out.error is not None and out.error["code"] == "home_build_untracked"


class TestAWatcherComesBack:
    @pytest.mark.asyncio
    async def test_a_build_still_driven_here_is_followed_and_opens_no_page(self, jobs, monkeypatch):
        root, _before = jobs
        state = _State(root)
        # The store of THIS process drives it: it adopted the job, so the reap spares it.
        job = _job(state.cloud_launch_store, status=lj.RUNNING)
        card = _waiting_card(job.id)
        seen: list[tuple[str, str, bool]] = []
        release = asyncio.Event()

        async def _watch(state_, card_id, job_id, *, may_open=False):
            seen.append((card_id, job_id, may_open))
            await release.wait()

        monkeypatch.setattr(setup_flow, "_watch_home", _watch)
        try:
            assert await setup_flow.resume_home_builds(state) == 1
            await asyncio.sleep(0)
            assert seen == [(card.id, job.id, False)]
            assert state.cloud_launch_store.get(job.id).status == lj.RUNNING
            assert sc.get_card(card.id).status == sc.STATUS_WAITING
        finally:
            release.set()
            await _drain()

    @pytest.mark.asyncio
    async def test_never_two_watchers_for_one_card(self, jobs, monkeypatch):
        root, _before = jobs
        state = _State(root)
        job = _job(state.cloud_launch_store, status=lj.RUNNING)
        card = _waiting_card(job.id)
        seen: list[str] = []
        release = asyncio.Event()

        async def _watch(state_, card_id, job_id, *, may_open=False):
            seen.append(card_id)
            await release.wait()

        monkeypatch.setattr(setup_flow, "_watch_home", _watch)
        try:
            assert await setup_flow.resume_home_builds(state) == 1
            assert await setup_flow.resume_home_builds(state) == 0
            assert setup_flow._start_home_watch(state, card.id, job.id, may_open=True) is False
            await asyncio.sleep(0)
            assert seen == [card.id]
        finally:
            release.set()
            await _drain()
        # Once it has ended, the card may be watched again.
        assert card.id not in setup_flow._home_watchers

    @pytest.mark.asyncio
    async def test_only_waiting_home_cards_with_a_build_are_picked_up(self, jobs, monkeypatch):
        root, before = jobs
        job = _job(before, status=lj.RUNNING)
        pending = _waiting_card(job.id)

        def _pending(c: sc.SetupCard) -> None:
            c.status = sc.STATUS_PENDING

        sc.update_card(pending.id, _pending)
        no_job = _waiting_card("")
        other = sc.create_card(
            slot="chat-1-1",
            session_key="dashboard:chat-1-1",
            kind=sc.KIND_SERVICE,
            payload={"platform": "linux"},
            private={"job_id": job.id},
        )
        sc.update_card(other.id, lambda c: setattr(c, "status", sc.STATUS_WAITING))
        started: list[str] = []

        async def _watch(state_, card_id, job_id, *, may_open=False):
            started.append(card_id)

        monkeypatch.setattr(setup_flow, "_watch_home", _watch)
        assert await setup_flow.resume_home_builds(_State(root)) == 0
        assert started == []
        assert sc.get_card(no_job.id).status == sc.STATUS_WAITING
