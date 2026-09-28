"""A job card's preview run: its approvals reach the card, and its verdict is honest.

A cron run's tool call that needs a person is a background approval with no slot.
Unless the card shows it, a preview waits until each request expires and then
reports ``success`` over a reply saying the commands were refused. These tests
pin:

* the gateway writes the run's session on the approval record as provenance
  only: it picks no slot and no trust grant speaks for it;
* while a preview runs, the card lists exactly that run's pending approvals;
* a preview whose approval was rejected or went unanswered is a ``failure``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from kiro_crew import setup_cards as sc
from kiro_crew.dashboard import setup_flow, setup_preview
from kiro_crew.dashboard.handlers import setup_cards as handlers
from kiro_crew.dashboard.interaction_coordinator import ApprovalCoordinator

JOB = "job123"
REPLY = "The git commands were rejected, so I can't produce the brief."


def _same(text: str) -> tuple[str, None]:
    return text, None


class _Slot:
    def __init__(self, key: str) -> None:
        self.key = key
        self.messages: list[Any] = []

    def append(self, role, content, cls="", ts="", *, broadcast=True, meta=None):
        self.messages.append((role, content, meta))


class PreviewState:
    """Enough of DashboardState for a card decision AND the real approval coordinator."""

    _BACKGROUND_APPROVAL_TIMEOUT_SECS = 5.0
    _APPROVAL_TIMEOUT = 5.0

    def __init__(self) -> None:
        self.slots = {"chat-1-1": _Slot("chat-1-1")}
        self._slots: dict[str, Any] = {}
        self._pending_approvals: dict[str, dict[str, Any]] = {}
        self._approval_futures: dict[str, asyncio.Future[bool]] = {}
        self._log = logging.getLogger("test_setup_preview")
        self.crons: Any = None
        self.events: list[tuple[str, Any]] = []

    def get_slot(self, key):
        return self.slots.get(key)

    def live_slot_count(self):
        return len(self.slots)

    def push_slots_update(self, **_):
        pass

    def broadcast_ws(self, msg_type, data):
        self.events.append((msg_type, data))

    def broadcast_ws_owners(self, msg_type, data):
        self.events.append((msg_type, data))

    def _audit_and_broadcast_approval(self, session_key, approval_id, approved, decision=""):
        self.events.append(("approval_resolved", {"id": approval_id, "approved": approved}))

    def resolve_state_approval(self, approval_id, approved):
        return ApprovalCoordinator.resolve_state(self, approval_id, approved)

    async def ask(self, approval_id: str, *, run_session: str, tool: str = "git log") -> bool:
        """Raise one background approval the way a cron run's callback does."""
        return await ApprovalCoordinator.request(
            self,
            approval_id,
            "cron",
            tool,
            tool_input=f"{tool} --oneline -5",
            tool_purpose="read recent commits",
            slot="",
            is_background=True,
            redact_url=_same,
            redact_secret=_same,
            run_session=run_session,
        )


class ApprovingCrons:
    """A cron service whose run asks for the approvals *asks* names, in order."""

    def __init__(self, state: PreviewState, asks: list[tuple[str, str]]) -> None:
        self.state = state
        self.asks = asks
        self.jobs: dict[str, Any] = {}
        self.answers: list[bool] = []

    async def add_job_async(self, name, message, **kw):
        job = MagicMock(id=JOB, last_result="", last_error="", last_status="")
        self.jobs[JOB] = job
        return job

    def discard_finished_run(self, jid):
        return True

    def is_running(self, jid):
        return False

    async def _run(self, jid):
        for approval_id, run_session in self.asks:
            self.answers.append(await self.state.ask(approval_id, run_session=run_session))
        job = self.jobs[jid]
        job.last_result = REPLY
        # An unanswered approval is not a security block, so the cron service
        # records the run ok: the reply alone says it was refused.
        job.last_status = "ok"
        return True

    def run_job(self, jid):
        return self._run(jid)

    def attach_run_task(self, jid, task):
        pass

    async def get_job_async(self, jid):
        return self.jobs[jid]


@pytest.fixture(autouse=True)
def _quiet_flow(monkeypatch):
    monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: None)

    async def _no_report(state, card):
        pass

    monkeypatch.setattr(setup_flow, "_report", _no_report)


async def _cron_card(state: PreviewState) -> sc.SetupCard:
    await setup_flow.propose(
        state,
        state.slots["chat-1-1"],
        "dashboard:chat-1-1",
        {
            "kind": "cron",
            "name": "Dev brief",
            "prompt": "Summarize my commits",
            "every_secs": 86400,
        },
        producer_is_user_facing=True,
    )
    (card,) = sc.list_cards("chat-1-1")
    return card


async def _answer_on_card(state: PreviewState, card_id: str, approved: bool) -> list[dict]:
    """Wait for the card to list an approval, then answer it the way the card does."""
    for _ in range(400):
        listed = setup_preview.pending_for_card(state, card_id)
        if listed:
            assert state.resolve_state_approval(listed[0]["id"], approved)
            return listed
        await asyncio.sleep(0.01)
    raise AssertionError("the card never listed the preview's approval")


class TestPreviewOnTheCard:
    @pytest.mark.asyncio
    async def test_an_approval_allowed_on_the_card_lets_the_preview_succeed(self):
        state = PreviewState()
        state.crons = ApprovingCrons(state, [("req-1", f"cron:{JOB}")])
        card = await _cron_card(state)
        answer = asyncio.create_task(_answer_on_card(state, card.id, True))
        previewed = await setup_flow.decide(state, card.id, "preview", card.payload_hash, {})
        listed = await answer
        assert [a["id"] for a in listed] == ["req-1"]
        assert listed[0]["tool"] == "git log"
        assert listed[0]["tool_input"] == "git log --oneline -5"
        assert state.crons.answers == [True]
        assert previewed.status == sc.STATUS_PENDING
        preview = previewed.outcome["preview"]
        assert preview["status"] == "success"
        assert preview["approvals"] == {
            "asked": 1,
            "allowed": 1,
            "rejected": 0,
            "unanswered": 0,
            "wait_secs": 5,
        }
        assert "reason" not in preview

    @pytest.mark.asyncio
    async def test_a_rejected_approval_makes_the_preview_a_failure(self):
        state = PreviewState()
        state.crons = ApprovingCrons(state, [("req-1", f"cron:{JOB}")])
        card = await _cron_card(state)
        answer = asyncio.create_task(_answer_on_card(state, card.id, False))
        previewed = await setup_flow.decide(state, card.id, "preview", card.payload_hash, {})
        await answer
        preview = previewed.outcome["preview"]
        assert preview["status"] == "failure"
        assert preview["reason"] == setup_preview.REASON_APPROVAL_NOT_GIVEN
        assert preview["approvals"]["rejected"] == 1
        assert preview["text"] == REPLY

    @pytest.mark.asyncio
    async def test_an_unanswered_approval_makes_the_preview_a_failure(self, monkeypatch):
        monkeypatch.setattr(setup_preview, "_WATCH_POLL_SECS", 0.01)
        state = PreviewState()
        state._BACKGROUND_APPROVAL_TIMEOUT_SECS = 0.2
        state.crons = ApprovingCrons(state, [("req-1", f"cron:{JOB}")])
        card = await _cron_card(state)
        previewed = await setup_flow.decide(state, card.id, "preview", card.payload_hash, {})
        assert state.crons.answers == [False]
        preview = previewed.outcome["preview"]
        assert preview["status"] == "failure"
        assert preview["reason"] == setup_preview.REASON_APPROVAL_NOT_GIVEN
        assert preview["approvals"]["unanswered"] == 1
        assert preview["approvals"]["wait_secs"] == 0

    @pytest.mark.asyncio
    async def test_the_card_lists_only_its_own_runs_approvals(self):
        state = PreviewState()
        state._BACKGROUND_APPROVAL_TIMEOUT_SECS = 0.3
        state.crons = ApprovingCrons(state, [])
        card = await _cron_card(state)
        seen: list[list[str]] = []

        async def _run(jid):
            others = [
                asyncio.create_task(state.ask("other-job", run_session="cron:other")),
                asyncio.create_task(state.ask("unowned", run_session="")),
                asyncio.create_task(state.ask("mine", run_session=f"cron:{JOB}:agent")),
            ]
            await asyncio.sleep(0.1)
            seen.append([a["id"] for a in setup_preview.pending_for_card(state, card.id)])
            await asyncio.gather(*others)
            state.crons.jobs[jid].last_result = "done"
            state.crons.jobs[jid].last_status = "ok"
            return True

        state.crons.run_job = _run
        previewed = await setup_flow.decide(state, card.id, "preview", card.payload_hash, {})
        assert seen == [["mine"]]
        assert previewed.outcome["preview"]["approvals"]["asked"] == 1
        assert setup_preview.pending_for_card(state, card.id) == []

    @pytest.mark.asyncio
    async def test_a_preview_that_asked_nothing_keeps_its_shape(self):
        state = PreviewState()
        state.crons = ApprovingCrons(state, [])
        card = await _cron_card(state)
        previewed = await setup_flow.decide(state, card.id, "preview", card.payload_hash, {})
        assert previewed.outcome["preview"] == {"status": "success", "text": REPLY}


class TestPreviewOutcome:
    def test_a_run_that_already_failed_keeps_its_status(self):
        tally = setup_preview.ApprovalTally(unanswered=2)
        out = setup_preview.preview_outcome(PreviewState(), "timeout", "t", tally)
        assert out["status"] == "timeout"
        assert "reason" not in out
        assert out["approvals"]["asked"] == 2

    def test_every_approval_allowed_is_still_a_success(self):
        tally = setup_preview.ApprovalTally(allowed=3)
        out = setup_preview.preview_outcome(PreviewState(), "success", "t", tally)
        assert out["status"] == "success"
        assert out["approvals"]["allowed"] == 3


class TestApprovalRecord:
    @pytest.mark.asyncio
    async def test_the_run_session_rides_the_record_only_when_given(self):
        state = PreviewState()
        mine = asyncio.create_task(state.ask("a", run_session="cron:j1"))
        other = asyncio.create_task(state.ask("b", run_session=""))
        await asyncio.sleep(0)
        assert state._pending_approvals["a"]["run_session"] == "cron:j1"
        assert state._pending_approvals["a"]["slot"] == ""
        assert "run_session" not in state._pending_approvals["b"]
        state.resolve_state_approval("a", True)
        state.resolve_state_approval("b", True)
        assert await mine and await other

    @pytest.mark.asyncio
    async def test_the_gateway_passes_provenance_without_a_slot_or_trust(self):
        from test_slack_gateway import _make_orchestrator, _mock_dashboard_state

        orch = _make_orchestrator(slack_enabled=False)
        ds = _mock_dashboard_state()
        # A trusted tab for the same job must not speak for the unattended run:
        # the run's session is provenance, never the owning slot.
        ds._slots = {"cron-j1": MagicMock(_trust=True)}
        ds.request_approval = AsyncMock(return_value=False)
        orch.dashboard_state = ds
        callback = orch._interactive_approval("cron", run_session_key="cron:j1")
        event = MagicMock(request_id="req-9", title="git log", tool_input="", tool_purpose="")
        event.child_low_fidelity = False
        with (
            patch("kiro_crew.slack.handler.is_yolo_mode", return_value=False),
            patch("kiro_crew.sel.sel"),
        ):
            assert await callback(event) is False
        ds.request_approval.assert_awaited_once()
        kwargs = ds.request_approval.await_args.kwargs
        assert kwargs["run_session"] == "cron:j1"
        assert kwargs["slot"] == ""
        assert kwargs["is_background"] is True

    @pytest.mark.parametrize(
        ("sequence", "expected"),
        [([], "cron:g1"), (["a", "b"], "cron:g1:a")],
    )
    def test_a_cron_run_names_its_own_session(self, sequence, expected):
        from test_cron_refusal_status import APPROVED, _run_cron_runs

        factory = MagicMock(return_value="interactive_cb")
        _run_cron_runs([APPROVED], agent_sequence=sequence, approval_factory=factory)
        factory.assert_any_call("cron", run_session_key=expected)


def _app() -> web.Application:
    app = web.Application()
    app["state"] = PreviewState()
    handlers.register_routes(app)
    return app


class TestCardApprovalsRoute:
    @pytest.mark.asyncio
    async def test_a_stranger_cannot_read_a_cards_approvals(self, monkeypatch):
        async def _gate(request, operation):
            return web.json_response({"error": "owner only", "code": "owner_only"}, status=403)

        monkeypatch.setattr(handlers, "require_owner_dashboard_request", _gate)
        card = sc.create_card(
            slot="chat-1-1", session_key="dashboard:chat-1-1", kind=sc.KIND_CRON, payload={}
        )
        async with TestClient(TestServer(_app())) as client:
            r = await client.get(f"/api/setup/cards/{card.id}/approvals")
            assert r.status == 403

    @pytest.mark.asyncio
    async def test_the_owner_reads_the_running_previews_approvals(self, monkeypatch):
        async def _gate(request, operation):
            return None

        monkeypatch.setattr(handlers, "require_owner_dashboard_request", _gate)
        card = sc.create_card(
            slot="chat-1-1", session_key="dashboard:chat-1-1", kind=sc.KIND_CRON, payload={}
        )
        app = _app()
        state: PreviewState = app["state"]
        async with TestClient(TestServer(app)) as client:
            r = await client.get("/api/setup/cards/sc-0000000000000000/approvals")
            assert r.status == 404
            r = await client.get(f"/api/setup/cards/{card.id}/approvals")
            assert (await r.json()) == {"approvals": []}
            watch = setup_preview.ApprovalWatch(state, card.id, JOB).start()
            asked = asyncio.create_task(state.ask("req-1", run_session=f"cron:{JOB}"))
            await asyncio.sleep(0)
            r = await client.get(f"/api/setup/cards/{card.id}/approvals")
            body = await r.json()
            assert [a["id"] for a in body["approvals"]] == ["req-1"]
            assert set(body["approvals"][0]) == {"id", "tool", "tool_input", "tool_purpose", "ts"}
            state.resolve_state_approval("req-1", True)
            assert await asked
            tally = await watch.stop()
            assert tally.allowed == 1
            r = await client.get(f"/api/setup/cards/{card.id}/approvals")
            assert (await r.json()) == {"approvals": []}
