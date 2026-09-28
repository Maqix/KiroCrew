"""A built home that never signed in to Kiro is not offered Move in (``setup_flow``).

A build can finish (``DONE``) with its sign-in step skipped: the device code ran
out unapproved, or a gateway restart cut the wait short. That home's agent cannot
answer, so the card stays in phase ``signin`` with "Sign the home in to Kiro",
which runs the same restart as the Instances hub (``handlers_cloud.restart_signin``)
and watches the build again. No AWS, no kiro-cli: the job store and the restart
are fakes.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kiro_crew import setup_cards as sc
from kiro_crew.cloud import launch_job as lj
from kiro_crew.dashboard import handlers_cloud, setup_flow
from kiro_crew.dashboard.home_signin import BROWSER_HERE_KEY

INSTANCE = "i-0abc123456789def0"


class FakeState:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def get_slot(self, key):
        return None

    def broadcast_ws_owners(self, msg_type, data):
        self.events.append((msg_type, data))

    def push_slots_update(self, **_):
        pass


class FakeStore:
    def __init__(self, jobs: list[lj.LaunchJob]) -> None:
        self.jobs = list(jobs)

    def get(self, job_id):
        return self.jobs.pop(0) if len(self.jobs) > 1 else self.jobs[0]


def _job(status: str = lj.DONE, *, signed: bool, signin: bool = False) -> lj.LaunchJob:
    job = lj.LaunchJob(
        id="job-1",
        profile="",
        region="eu-west-1",
        size_key="light",
        status=status,
        instance_id=INSTANCE,
        signin_detected=signed,
        signin=(
            lj.SigninPrompt(url="https://view.awsapps.com/start/#/device?user_code=A-B", code="A-B")
            if signin
            else None
        ),
    )
    if not signed:
        job.step(lj.STEP_SIGNIN).state = lj.STEP_SKIPPED
    return job


@pytest.fixture
def state():
    return FakeState()


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(setup_flow, "_CONNECT_POLL_SECS", 0)
    monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: None)
    monkeypatch.setattr(lj, "_issued_signins", {})


def _card(monkeypatch, *, phase: str = "build", status: str = sc.STATUS_WAITING, **private):
    from kiro_crew.cloud import iam

    monkeypatch.setattr(iam, "reachability_check", lambda p, r: {"reachable": True})
    payload, priv = setup_flow._home_payload(sc.build_home({"region": "eu-west-1"}))
    priv.update({"phase": phase, "job_id": "job-1", **private})
    card = sc.create_card(
        slot="chat-1-1",
        session_key="dashboard:chat-1-1",
        kind=sc.KIND_HOME,
        payload=payload,
        private=priv,
    )

    def _set(c: sc.SetupCard) -> None:
        c.status = status

    return sc.update_card(card.id, _set)


async def _watch(monkeypatch, state, card, jobs) -> sc.SetupCard:
    store = FakeStore(jobs)
    monkeypatch.setattr(handlers_cloud, "_store", lambda st: store)
    await asyncio.wait_for(setup_flow._watch_home(state, card.id, "job-1"), 5)
    return sc.get_card(card.id)


@pytest.fixture
def restart(monkeypatch):
    """The sign-in restart the card runs, and the watcher it starts after."""
    world: dict[str, Any] = {"calls": [], "answer": None, "watched": []}

    async def _restart(state_, job_id):
        world["calls"].append(job_id)
        answer = world["answer"]
        if isinstance(answer, handlers_cloud.LaunchRefusal):
            return None, answer
        return answer or _job(lj.RUNNING, signed=False), None

    async def _watch_home(state_, card_id, job_id, *, may_open=False):
        world["watched"].append((card_id, job_id, may_open))

    monkeypatch.setattr(handlers_cloud, "restart_signin", _restart)
    monkeypatch.setattr(setup_flow, "_watch_home", _watch_home)
    return world


class TestTheBuildEnds:
    @pytest.mark.asyncio
    async def test_an_unsigned_home_is_not_offered_move_in(self, state, monkeypatch):
        card = _card(monkeypatch)
        done = await _watch(monkeypatch, state, card, [_job(signed=False, signin=True)])
        assert done.status == sc.STATUS_PENDING
        assert done.private["phase"] == "signin"
        assert done.private["instance_id"] == INSTANCE
        assert done.outcome["ready"] is False and done.outcome["needs_signin"] is True
        assert [s["key"] for s in done.outcome["steps"]][2] == lj.STEP_SIGNIN
        # The code the job still holds stays on the card.
        assert done.outcome["signin"]["code"] == "A-B"

    @pytest.mark.asyncio
    async def test_a_signed_in_home_is_offered_move_in(self, state, monkeypatch):
        card = _card(monkeypatch)
        done = await _watch(monkeypatch, state, card, [_job(signed=True)])
        assert done.private["phase"] == "move"
        assert done.outcome["ready"] is True and "needs_signin" not in done.outcome


class TestSignTheHomeIn:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("here", [True, False])
    async def test_the_commit_restarts_the_sign_in_and_watches_again(
        self, state, monkeypatch, restart, here
    ):
        from kiro_crew.dashboard import setup_aws_signin

        monkeypatch.setattr(setup_aws_signin, "browser_is_here", lambda same_machine: here)
        # A card left at needs_signin, as a gateway restart leaves it: nothing in
        # memory, only the stored card.
        card = _card(monkeypatch, phase="signin", status=sc.STATUS_PENDING)
        out = await setup_flow.decide(
            state, card.id, "commit", card.payload_hash, {}, same_machine=here
        )
        await asyncio.sleep(0)  # the watcher task starts
        assert restart["calls"] == ["job-1"]
        assert out.status == sc.STATUS_WAITING
        assert restart["watched"] == [(card.id, "job-1", here)]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "code",
        ["login_target_unreadable", "launch_already_running", "launch_has_no_instance"],
    )
    async def test_a_refused_restart_is_the_cards_error(self, state, monkeypatch, restart, code):
        restart["answer"] = handlers_cloud.LaunchRefusal({"error": "no", "code": code}, 409)
        card = _card(monkeypatch, phase="signin", status=sc.STATUS_PENDING)
        out = await setup_flow.decide(state, card.id, "commit", card.payload_hash, {})
        assert out.status == sc.STATUS_PENDING and out.error["code"] == code
        assert out.private["phase"] == "signin"
        assert restart["watched"] == []

    @pytest.mark.asyncio
    async def test_a_home_signed_in_meanwhile_goes_to_move_in(self, state, monkeypatch, restart):
        restart["answer"] = handlers_cloud.LaunchRefusal(
            {"error": "already", "code": "signin_already_complete"}, 409
        )
        monkeypatch.setattr(handlers_cloud, "_store", lambda st: FakeStore([_job(signed=True)]))
        card = _card(monkeypatch, phase="signin", status=sc.STATUS_PENDING)
        out = await setup_flow.decide(state, card.id, "commit", card.payload_hash, {})
        assert out.status == sc.STATUS_PENDING and out.error is None
        assert out.private["phase"] == "move" and out.outcome["ready"] is True

    @pytest.mark.asyncio
    async def test_a_card_with_no_build_says_so(self, state, monkeypatch, restart):
        card = _card(monkeypatch, phase="signin", status=sc.STATUS_PENDING, job_id="")
        out = await setup_flow.decide(state, card.id, "commit", card.payload_hash, {})
        assert out.error["code"] == "home_signin_unavailable"
        assert restart["calls"] == []

    @pytest.mark.asyncio
    async def test_the_click_records_where_the_browser_is(self, state, monkeypatch, restart):
        from kiro_crew.dashboard import setup_aws_signin

        monkeypatch.setattr(setup_aws_signin, "browser_is_here", lambda same_machine: True)
        card = _card(monkeypatch, phase="signin", status=sc.STATUS_PENDING)
        await setup_flow.decide(state, card.id, "commit", card.payload_hash, {}, same_machine=True)
        assert sc.get_card(card.id).private[BROWSER_HERE_KEY] is True
