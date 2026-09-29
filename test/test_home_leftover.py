"""What a home build a gateway restart cut short may have left in AWS.

No worker is left to roll its stack back, so the failed card says so
(``outcome.leftover``, or an untracked build with ``stopped: false``), and its
``remove`` decision runs the Instances hub's destroy for the build's own tag
(``handlers_cloud.teardown_stack``), once, when the owner clicks it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from kiro_crew import setup_cards as sc
from kiro_crew.cloud import ec2
from kiro_crew.cloud import launch_job as lj
from kiro_crew.dashboard import handlers_cloud, setup_flow

TAG = "kc-home-7f3a"


class _State:
    def __init__(self, root: Path) -> None:
        self.events: list = []
        self.cloud_launch_store = lj.LaunchJobStore(root=root)

    def get_slot(self, key):
        return None

    def broadcast_ws_owners(self, msg_type, data):
        self.events.append((msg_type, data))


@pytest.fixture
def state(tmp_path, monkeypatch) -> _State:
    monkeypatch.setattr(setup_flow, "_CONNECT_POLL_SECS", 0)
    monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: None)
    return _State(tmp_path / "jobs")


@pytest.fixture
def teardown(monkeypatch):
    """Every teardown the removal runs, and what AWS answers."""
    world: dict[str, Any] = {"calls": [], "answer": True, "release": None}

    def _teardown(store, tag, profile, region):
        world["calls"].append((tag, profile, region))
        if world["release"] is not None:
            world["release"].wait(5)
        if isinstance(world["answer"], Exception):
            raise world["answer"]
        return world["answer"]

    monkeypatch.setattr(handlers_cloud, "teardown_stack", _teardown)
    return world


def _interrupted(store: lj.LaunchJobStore, *, provisioned: bool = True) -> lj.LaunchJob:
    """A job the restart's reap failed, written by the store of the process that ran it."""
    before = lj.LaunchJobStore(root=store.root)
    job = before.create(profile="", region="eu-north-1", size_key="lite")
    job.tag = TAG
    job.status = lj.RUNNING
    job.step(lj.STEP_PREFLIGHT).state = lj.STEP_DONE
    job.step(lj.STEP_PROVISION).state = lj.STEP_ACTIVE if provisioned else lj.STEP_PENDING
    if not provisioned:
        job.step(lj.STEP_PREFLIGHT).state = lj.STEP_ACTIVE
    before.save(job)
    store.reap_orphans()
    reaped = store.get(job.id)
    assert reaped is not None and lj.interrupted_by_restart(reaped)
    return reaped


def _card(job_id: str, *, status: str = sc.STATUS_WAITING, outcome=None) -> sc.SetupCard:
    card = sc.create_card(
        slot="chat-1-1",
        session_key="dashboard:chat-1-1",
        kind=sc.KIND_HOME,
        payload={"simulated": False, "region": "eu-north-1"},
        private={
            "settings": {"region": "eu-north-1", "profile": "", "size": "lite"},
            "phase": "build",
            "job_id": job_id,
        },
    )

    def _set(c: sc.SetupCard) -> None:
        c.status = status
        if outcome is not None:
            c.outcome = outcome
        if status == sc.STATUS_FAILED:
            c.error = {"code": "home_build_failed", "message": lj.RESTART_INTERRUPTED}

    return sc.update_card(card.id, _set)


async def _until(card_id: str, check, *, timeout: float = 5.0) -> sc.SetupCard:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        card = sc.get_card(card_id)
        assert card is not None
        if check(card):
            return card
        assert asyncio.get_running_loop().time() < deadline, "the card never got there"
        await asyncio.sleep(0.01)


def _removal(card: sc.SetupCard) -> str:
    return str(((card.outcome or {}).get("removal") or {}).get("state") or "")


class TestTheCardSaysWhatMayBeLeft:
    @pytest.mark.asyncio
    async def test_a_build_cut_short_after_its_stack_began_names_it(self, state):
        job = _interrupted(state.cloud_launch_store)
        card = _card(job.id)
        await setup_flow.resume_home_builds(state)
        out = await _until(card.id, lambda c: c.status == sc.STATUS_FAILED)
        assert out.error is not None and out.error["code"] == "home_build_failed"
        assert out.outcome is not None
        assert out.outcome["leftover"] == {
            "tag": TAG,
            "stack": ec2.stack_name(TAG),
            "region": "eu-north-1",
        }

    @pytest.mark.asyncio
    async def test_a_build_cut_short_before_any_stack_names_none(self, state):
        job = _interrupted(state.cloud_launch_store, provisioned=False)
        card = _card(job.id)
        await setup_flow.resume_home_builds(state)
        out = await _until(card.id, lambda c: c.status == sc.STATUS_FAILED)
        assert "leftover" not in (out.outcome or {})

    @pytest.mark.asyncio
    async def test_an_untracked_build_nothing_stopped_does_not_claim_a_removal(self, state):
        card = _card("0123456789ab")
        await setup_flow.resume_home_builds(state)
        out = await _until(card.id, lambda c: c.status == sc.STATUS_FAILED)
        assert out.outcome == {"job_id": "0123456789ab", "stopped": False}
        assert out.error is not None and out.error["code"] == "home_build_untracked"
        assert "is being removed" not in out.error["message"]
        assert "may still be in the AWS account" in out.error["message"]

    @pytest.mark.asyncio
    async def test_an_untracked_build_stopped_here_keeps_its_words(self, state):
        import threading

        card = _card("0123456789ab")
        handlers_cloud._cancels(state)["0123456789ab"] = threading.Event()
        await setup_flow._stop_untracked_build(state, card.id, "0123456789ab")
        out = sc.get_card(card.id)
        assert out.outcome == {"job_id": "0123456789ab", "stopped": True}
        assert "is being removed" in out.error["message"]


class TestTheRemoveDecision:
    @pytest.mark.asyncio
    async def test_the_owners_click_runs_the_teardown_once(self, state, teardown):
        job = _interrupted(state.cloud_launch_store)
        card = _card(job.id, status=sc.STATUS_FAILED, outcome={"leftover": {"tag": TAG}})
        out = await setup_flow.decide(state, card.id, "remove", card.payload_hash, {"tag": TAG})
        assert out.status == sc.STATUS_FAILED and _removal(out) == "active"
        # The card's words and the stack it names are the build's own.
        assert out.error == card.error
        assert out.outcome is not None and out.outcome["leftover"]["stack"] == ec2.stack_name(TAG)
        done = await _until(card.id, lambda c: _removal(c) == "done")
        assert teardown["calls"] == [(TAG, "", "eu-north-1")]
        assert done.status == sc.STATUS_FAILED and done.error == card.error
        assert state.events[-1][1]["card"]["outcome"]["removal"] == {"state": "done"}
        # Done is done: another click deletes nothing.
        with pytest.raises(sc.CardRejected) as again:
            await setup_flow.decide(state, card.id, "remove", card.payload_hash, {"tag": TAG})
        assert again.value.code == "home_remove_running"
        assert len(teardown["calls"]) == 1

    @pytest.mark.asyncio
    async def test_a_second_click_while_it_runs_starts_nothing(self, state, teardown):
        import threading

        teardown["release"] = threading.Event()
        job = _interrupted(state.cloud_launch_store)
        card = _card(job.id, status=sc.STATUS_FAILED)
        try:
            await setup_flow.decide(state, card.id, "remove", card.payload_hash, {"tag": TAG})
            with pytest.raises(sc.CardRejected) as busy:
                await setup_flow.decide(state, card.id, "remove", card.payload_hash, {"tag": TAG})
            assert busy.value.code == "home_remove_running"
        finally:
            teardown["release"].set()
        await _until(card.id, lambda c: _removal(c) == "done")
        assert len(teardown["calls"]) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "answer", [False, RuntimeError("DeleteStack AccessDenied 123456789012")]
    )
    async def test_a_removal_that_does_not_finish_says_so_and_can_run_again(
        self, state, teardown, answer
    ):
        teardown["answer"] = answer
        job = _interrupted(state.cloud_launch_store)
        card = _card(job.id, status=sc.STATUS_FAILED)
        await setup_flow.decide(state, card.id, "remove", card.payload_hash, {"tag": TAG})
        out = await _until(card.id, lambda c: _removal(c) == "failed")
        # The AWS error stays in the log: it can name the whole account id.
        assert "123456789012" not in str(out.outcome)
        teardown["answer"] = True
        await setup_flow.decide(state, card.id, "remove", card.payload_hash, {"tag": TAG})
        await _until(card.id, lambda c: _removal(c) == "done")
        assert len(teardown["calls"]) == 2

    @pytest.mark.asyncio
    async def test_a_stale_hash_removes_nothing(self, state, teardown):
        job = _interrupted(state.cloud_launch_store)
        card = _card(job.id, status=sc.STATUS_FAILED)
        with pytest.raises(sc.CardRejected) as exc:
            await setup_flow.decide(state, card.id, "remove", "0" * 64, {"tag": TAG})
        assert exc.value.code == "card_hash_mismatch"
        assert teardown["calls"] == [] and _removal(sc.get_card(card.id)) == ""

    @pytest.mark.asyncio
    async def test_a_stack_the_card_did_not_show_is_refused(self, state, teardown):
        job = _interrupted(state.cloud_launch_store)
        card = _card(job.id, status=sc.STATUS_FAILED)
        with pytest.raises(sc.CardRejected) as exc:
            await setup_flow.decide(
                state, card.id, "remove", card.payload_hash, {"tag": "another-crew"}
            )
        assert exc.value.code == "card_hash_mismatch" and teardown["calls"] == []

    @pytest.mark.asyncio
    async def test_governance_can_refuse_it(self, state, teardown, monkeypatch):
        job = _interrupted(state.cloud_launch_store)
        card = _card(job.id, status=sc.STATUS_FAILED)
        monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: "no")
        with pytest.raises(sc.CardRejected) as exc:
            await setup_flow.decide(state, card.id, "remove", card.payload_hash, {"tag": TAG})
        assert exc.value.code == "governance_denied" and teardown["calls"] == []

    @pytest.mark.asyncio
    async def test_only_a_build_a_restart_cut_short_after_its_stack_began(self, state, teardown):
        store = state.cloud_launch_store
        # Before any stack: nothing to remove.
        early = _interrupted(store, provisioned=False)
        # A build that failed on its own: its worker already rolled the stack back.
        rolled = _interrupted(store)
        rolled.error = "Provisioning failed. The home kc-home-7f3a was removed so it stops billing."
        store.save(rolled)
        cases = [
            _card(early.id, status=sc.STATUS_FAILED),
            _card(rolled.id, status=sc.STATUS_FAILED),
            _card("0123456789ab", status=sc.STATUS_FAILED),
            _card(_interrupted(store).id, status=sc.STATUS_PENDING),
        ]
        for card in cases:
            with pytest.raises(sc.CardRejected) as exc:
                await setup_flow.decide(state, card.id, "remove", card.payload_hash, {"tag": TAG})
            assert exc.value.code == "home_nothing_to_remove", card.id
        assert teardown["calls"] == []


class TestARestartDuringTheRemoval:
    @pytest.mark.asyncio
    async def test_its_card_offers_the_removal_again(self, state):
        card = _card(
            "0123456789ab",
            status=sc.STATUS_FAILED,
            outcome={"leftover": {"tag": TAG}, "removal": {"state": "active"}},
        )
        assert await setup_flow.resume_home_builds(state) == 0
        assert _removal(sc.get_card(card.id)) == "failed"


class TestTheTeardownIsTheInstancesHubs:
    def test_the_stack_first_then_its_local_state_once_aws_confirms(self, tmp_path, monkeypatch):
        calls: list[Any] = []
        monkeypatch.setattr(ec2, "describe", lambda t, p, r: {"instance_id": "i-0abc"})
        monkeypatch.setattr(
            ec2, "destroy", lambda t, p, r, wait=True: calls.append(("destroy", t, wait)) or {}
        )
        monkeypatch.setattr(ec2, "wait_for_delete", lambda t, p, r: calls.append("wait") or True)
        monkeypatch.setattr(
            handlers_cloud.connect_mod,
            "unregister_instance",
            lambda iid: calls.append(("unregister", iid)),
        )
        monkeypatch.setattr(
            handlers_cloud.source_mod,
            "delete_source",
            lambda t, p, r: calls.append(("source", t)) or {"removed": True},
        )
        store = lj.LaunchJobStore(root=tmp_path / "jobs")
        assert handlers_cloud.teardown_stack(store, TAG, "", "eu-north-1") is True
        assert calls == [
            ("destroy", TAG, False),
            "wait",
            ("unregister", "i-0abc"),
            ("source", TAG),
        ]

    def test_an_unconfirmed_delete_keeps_the_local_state(self, tmp_path, monkeypatch):
        calls: list[Any] = []
        monkeypatch.setattr(ec2, "describe", lambda t, p, r: {"instance_id": "i-0abc"})
        monkeypatch.setattr(ec2, "destroy", lambda t, p, r, wait=True: {})
        monkeypatch.setattr(ec2, "wait_for_delete", lambda t, p, r: False)
        monkeypatch.setattr(
            handlers_cloud.connect_mod, "unregister_instance", lambda iid: calls.append(iid)
        )
        monkeypatch.setattr(
            handlers_cloud.source_mod, "delete_source", lambda t, p, r: calls.append(t)
        )
        store = lj.LaunchJobStore(root=tmp_path / "jobs")
        assert handlers_cloud.teardown_stack(store, TAG, "", "eu-north-1") is False
        assert calls == []
