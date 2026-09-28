"""The main chat hears when a chat it handed work to finishes (RFC §6.9, MC.9)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from kiro_crew import first_run
from kiro_crew.dashboard import chat_runner
from kiro_crew.dashboard import handoff_notice as hn
from kiro_crew.dashboard.state import DashboardState, _ChatSlot
from kiro_crew.dashboard.system_notices import HANDOFF_DONE_KIND, SYSTEM_NOTICE_KINDS
from kiro_crew.history import ConversationLog

MAIN = "chat-main-1"
CHILD = "chat-child-1"


def _state(tmp_path) -> DashboardState:
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_slack_link = MagicMock(return_value=(None, None))
    sessions.get_mirror_link = MagicMock(return_value=None)
    sessions.get_provider = MagicMock(return_value=None)
    sessions.resumable_sid = MagicMock(return_value=None)
    sessions.allocation_requested_model = MagicMock(return_value="")
    sessions.reset = AsyncMock()
    state = DashboardState(
        sessions=sessions,
        crons=MagicMock(list_jobs=MagicMock(return_value=[]), status=MagicMock(return_value={})),
        lessons=MagicMock(load_all=MagicMock(return_value=[])),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )
    state.broadcast_ws = MagicMock()
    state.push_slots_update = MagicMock()
    state.push_refresh = MagicMock()
    state.refresh_slot_source_status = MagicMock()
    return state


def _slot(state: DashboardState, key: str, *, title: str, created_by: str = "") -> _ChatSlot:
    slot = _ChatSlot(key)
    slot.title = title
    # Titled, so the end-of-cycle titling self-guards without a model call.
    slot._titled = True
    slot._created_by = created_by
    state._slots[key] = slot
    return slot


def _replied(slot: _ChatSlot, reply: str = "Found three flaky tests.") -> None:
    slot.append("user", "Look into the flaky tests.", "msg msg-u")
    slot.append("assistant", reply, "msg msg-a")


def _errored(slot: _ChatSlot) -> None:
    slot.append("user", "Look into the flaky tests.", "msg msg-u")
    slot.append("error", "The model is unavailable.", "msg msg-err")


def _stopped(slot: _ChatSlot) -> None:
    """The row every Stop press (and ``session_stop``) writes: JSON in ``cls``."""
    stop = json.dumps({"kind": "stop_event", "id": "stop-1", "state": "stopped", "outcome": "soft"})
    slot.append("system", stop, stop)


def _new_turn(slot: _ChatSlot) -> None:
    slot.task = asyncio.get_running_loop().create_future()
    slot.task = None


def _notices(slot: _ChatSlot) -> list[dict]:
    return [
        m
        for m in slot.messages
        if m.get("role") == "assistant" and (m.get("meta") or {}).get("kind") == HANDOFF_DONE_KIND
    ]


def _meta(notice: dict) -> dict:
    meta = notice["meta"]
    return {k: meta[k] for k in ("kind", "slot", "title", "outcome")}


def _held_meta(outcome: str = "done", title: str = "Flaky tests") -> dict:
    return {"kind": HANDOFF_DONE_KIND, "slot": CHILD, "title": title, "outcome": outcome}


async def _settle() -> None:
    for _ in range(5):
        if not hn._tasks:
            return
        await asyncio.gather(*list(hn._tasks))


@pytest.fixture
def home(tmp_path):
    first_run.record_slot(MAIN)
    first_run.record_main(MAIN)
    state = _state(tmp_path)
    main = _slot(state, MAIN, title="Nova")
    return state, main


def test_the_kind_is_a_system_notice():
    assert HANDOFF_DONE_KIND in SYSTEM_NOTICE_KINDS


class TestTheMainChatIsTold:
    @pytest.mark.asyncio
    async def test_a_handed_off_chat_that_finishes_posts_one_notice(self, home):
        state, main = home
        child = _slot(state, CHILD, title="Flaky tests", created_by=MAIN)
        _replied(child)

        await chat_runner._finish_queue_cycle(state, child)
        await _settle()

        notices = _notices(main)
        assert len(notices) == 1
        assert _meta(notices[0]) == _held_meta("done")
        assert notices[0]["content"] == "“Flaky tests” finished. Ask me here for what it found."
        assert notices[0]["cls"] == "msg msg-system"
        # A notice is not a turn: nothing started in the main chat.
        assert main.task is None
        assert not _notices(child)

    @pytest.mark.asyncio
    async def test_a_turn_that_ends_on_an_error_says_so(self, home):
        state, main = home
        child = _slot(state, CHILD, title="Flaky tests", created_by=MAIN)
        _errored(child)

        await chat_runner._finish_queue_cycle(state, child)
        await _settle()

        notices = _notices(main)
        assert len(notices) == 1
        assert _meta(notices[0]) == _held_meta("error")
        assert notices[0]["content"] == (
            "“Flaky tests” stopped with an error. Open it to see what happened."
        )

    @pytest.mark.asyncio
    async def test_a_reply_outranks_a_later_error(self, home):
        state, main = home
        child = _slot(state, CHILD, title="Flaky tests", created_by=MAIN)
        _replied(child)
        child.append("error", "The connection dropped.", "msg msg-err")
        await chat_runner._finish_queue_cycle(state, child)
        await _settle()
        assert [_meta(n)["outcome"] for n in _notices(main)] == ["done"]

    @pytest.mark.asyncio
    async def test_at_most_one_notice_per_turn(self, home):
        state, main = home
        child = _slot(state, CHILD, title="Flaky tests", created_by=MAIN)
        _replied(child)

        await chat_runner._finish_queue_cycle(state, child)
        await _settle()
        # The same turn reported again (a second cycle end with no new turn).
        main.append("user", "thanks", "msg msg-u")
        hn.note_cycle_end(state, child)
        await _settle()
        assert len(_notices(main)) == 1

    @pytest.mark.asyncio
    async def test_notices_do_not_stack_while_the_main_chat_is_quiet(self, home):
        """A user talking in the side chat directly does not pile notices up."""
        state, main = home
        child = _slot(state, CHILD, title="Flaky tests", created_by=MAIN)
        _replied(child)
        await chat_runner._finish_queue_cycle(state, child)
        await _settle()

        _new_turn(child)
        _replied(child, "And a fourth one.")
        await chat_runner._finish_queue_cycle(state, child)
        await _settle()
        assert len(_notices(main)) == 1

        # A different outcome is news even while the main chat is quiet.
        _new_turn(child)
        _errored(child)
        await chat_runner._finish_queue_cycle(state, child)
        await _settle()
        assert [_meta(n)["outcome"] for n in _notices(main)] == ["done", "error"]

        # Once the main chat moves on, the next finished turn is news again.
        main.append("user", "what else?", "msg msg-u")
        main.append("assistant", "Nothing else.", "msg msg-a")
        _new_turn(child)
        _replied(child, "Fixed them.")
        await chat_runner._finish_queue_cycle(state, child)
        await _settle()
        assert len(_notices(main)) == 3

    @pytest.mark.asyncio
    async def test_a_notice_due_mid_turn_waits_for_the_main_chats_turn_to_end(self, home):
        state, main = home
        child = _slot(state, CHILD, title="Flaky tests", created_by=MAIN)
        _replied(child)
        running = asyncio.get_running_loop().create_future()
        main.task = running

        await chat_runner._finish_queue_cycle(state, child)
        await _settle()
        assert not _notices(main)

        main.append("assistant", "Here is the plan.", "msg msg-a")
        running.set_result(None)
        await chat_runner._finish_queue_cycle(state, main)
        await _settle()
        notices = _notices(main)
        assert len(notices) == 1
        # Below the reply the main chat was writing, never inside it.
        rows = [m for m in main.messages if m.get("role") != "done"]
        assert rows[-1] is notices[0]
        assert first_run.read_handoff_owed() == {}


class TestNothingIsPosted:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("created_by", ["", "chat-other-1", "cron-nightly"])
    async def test_for_a_chat_the_main_chat_did_not_create(self, home, created_by):
        state, main = home
        child = _slot(state, CHILD, title="Elsewhere", created_by=created_by)
        _replied(child)
        await chat_runner._finish_queue_cycle(state, child)
        await _settle()
        assert not _notices(main)

    @pytest.mark.asyncio
    async def test_when_the_main_chat_itself_ran(self, home):
        state, main = home
        main._created_by = MAIN
        _replied(main)
        await chat_runner._finish_queue_cycle(state, main)
        await _settle()
        assert not _notices(main)

    @pytest.mark.asyncio
    async def test_without_a_main_chat(self, tmp_path):
        first_run.record_slot(MAIN)
        state = _state(tmp_path)
        main = _slot(state, MAIN, title="Nova")
        child = _slot(state, CHILD, title="Flaky tests", created_by=MAIN)
        _replied(child)
        await chat_runner._finish_queue_cycle(state, child)
        await _settle()
        assert not _notices(main)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "ending", ["stopped", "stopped_after_a_reply", "stopped_after_an_error"]
    )
    async def test_for_a_turn_someone_stopped(self, home, ending):
        """Whoever pressed Stop (or called session_stop) already knows."""
        state, main = home
        child = _slot(state, CHILD, title="Flaky tests", created_by=MAIN)
        child.append("user", "Look into the flaky tests.", "msg msg-u")
        if ending == "stopped_after_a_reply":
            child.append("assistant", "Looking…", "msg msg-a")
        elif ending == "stopped_after_an_error":
            child.append("error", "The model is unavailable.", "msg msg-err")
        _stopped(child)
        await chat_runner._finish_queue_cycle(state, child)
        await _settle()
        assert not _notices(main)

    @pytest.mark.asyncio
    async def test_an_earlier_turns_stop_does_not_silence_this_one(self, home):
        state, main = home
        child = _slot(state, CHILD, title="Flaky tests", created_by=MAIN)
        child.append("user", "first try", "msg msg-u")
        _stopped(child)
        _replied(child)
        await chat_runner._finish_queue_cycle(state, child)
        await _settle()
        assert len(_notices(main)) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "waiting",
        ["question", "approval", "queued", "subagent", "no_word"],
    )
    async def test_while_the_chat_is_not_really_done(self, home, waiting):
        state, main = home
        child = _slot(state, CHILD, title="Flaky tests", created_by=MAIN)
        _replied(child)
        if waiting == "question":
            child._question_pending = {"card-1": {"ts": 0.0, "blocking": False}}
        elif waiting == "approval":
            child._approval_futures = {"a1": asyncio.get_running_loop().create_future()}
        elif waiting == "queued":
            child.queue_append("and then this")
        elif waiting == "subagent":
            state.subagents = MagicMock(running_agents_for=MagicMock(return_value=["agent-1"]))
        elif waiting == "no_word":
            child.append("user", "One more thing.", "msg msg-u")
        hn.note_cycle_end(state, child)
        await _settle()
        assert not _notices(main)


class TestAPlanEnding:
    @pytest.fixture(autouse=True)
    def _isolate_config_dir(self, tmp_path, monkeypatch):
        for module in ("state", "chat", "chat_orchestrator"):
            monkeypatch.setattr(f"kiro_crew.dashboard.{module}.config_dir", lambda: tmp_path)

    @pytest.mark.asyncio
    async def test_a_main_chat_plan_ending_delivers_what_it_held(self, home, monkeypatch):
        from kiro_crew.dashboard.chat_orchestrator import _stage_loop

        state, main = home
        main.mode = "orchestrator"
        main._auto_run = True
        main._stage_titles = ["First"]
        main._plan_goal = "Test goal"
        main._orch_tracker = None
        state.subagents = MagicMock(
            running_agents_for=MagicMock(return_value=[]),
            has_pending_work_for_async=AsyncMock(return_value=False),
            wait_for_parent_reports=AsyncMock(return_value=False),
        )
        seen_mid_plan: list[int] = []

        async def _stage_turn(state_, slot, message, **kwargs):
            callback = kwargs.get("_on_consumed")
            if callable(callback):
                callback(True)
            # The side chat finishes while the plan runs: the notice is held.
            hn._hold(hn._book(state, create=True), MAIN, _held_meta())
            slot.append("assistant", "stage done", "msg msg-a")
            seen_mid_plan.append(len(_notices(slot)))

        monkeypatch.setattr("kiro_crew.dashboard.chat_orchestrator._run_chat", _stage_turn)
        task = asyncio.create_task(_stage_loop(state, main, auto_run=True))
        main.track_stage_controller(task)
        main.task = task
        await task
        await asyncio.sleep(0)
        await _settle()

        assert seen_mid_plan == [0]
        notices = _notices(main)
        assert len(notices) == 1 and _meta(notices[0]) == _held_meta()
        assert first_run.read_handoff_owed() == {}

    @pytest.mark.asyncio
    async def test_the_plan_end_waits_for_the_controller_to_let_go(self, home):
        state, main = home
        hn._hold(hn._book(state, create=True), MAIN, _held_meta())
        release = asyncio.Event()

        async def _controller():
            await release.wait()

        task = asyncio.create_task(_controller())
        main.track_stage_controller(task)
        hn.note_controller_end(state, main, task, finished=True)
        await asyncio.sleep(0)
        assert not _notices(main)  # the controller still owns the slot
        release.set()
        await task
        await asyncio.sleep(0)
        assert len(_notices(main)) == 1

    @pytest.mark.asyncio
    async def test_a_paused_plan_is_not_reported_done(self, home):
        state, main = home
        child = _slot(state, CHILD, title="Flaky tests", created_by=MAIN)
        _replied(child)
        hn.note_controller_end(state, child, None, finished=False)
        await _settle()
        assert not _notices(main)
        hn.note_controller_end(state, child, None, finished=True)
        await _settle()
        assert len(_notices(main)) == 1


class TestARestart:
    @pytest.mark.asyncio
    async def test_a_held_notice_survives_a_restart(self, home, tmp_path):
        state, main = home
        child = _slot(state, CHILD, title="Flaky tests", created_by=MAIN)
        _errored(child)
        main.task = asyncio.get_running_loop().create_future()  # the main chat is mid-turn
        await chat_runner._finish_queue_cycle(state, child)
        await _settle()
        assert not _notices(main)
        assert first_run.read_handoff_owed() == {MAIN: {CHILD: _held_meta("error")}}

        # The gateway stops mid-turn; a new one restores the main chat idle.
        fresh = _state(tmp_path)
        restored = _slot(fresh, MAIN, title="Nova")
        restored.append("assistant", "Here is the plan.", "msg msg-a")
        assert await hn.restore_held(fresh) == 1
        await _settle()
        notices = _notices(restored)
        assert len(notices) == 1 and _meta(notices[0]) == _held_meta("error")
        assert first_run.read_handoff_owed() == {}

    @pytest.mark.asyncio
    async def test_a_restored_main_chat_that_is_busy_gets_it_at_its_cycle_end(self, home):
        state, main = home
        first_run.write_handoff_owed({MAIN: {CHILD: _held_meta()}})
        running = asyncio.get_running_loop().create_future()
        main.task = running
        assert await hn.restore_held(state) == 0
        await _settle()
        assert first_run.read_handoff_owed() == {MAIN: {CHILD: _held_meta()}}

        running.set_result(None)
        await chat_runner._finish_queue_cycle(state, main)
        await _settle()
        assert len(_notices(main)) == 1
        assert first_run.read_handoff_owed() == {}

    @pytest.mark.asyncio
    async def test_the_restore_dedupes_against_the_newest_row(self, home):
        state, main = home
        main.append(
            "assistant",
            "“Flaky tests” finished. Ask me here for what it found.",
            "msg msg-system",
            meta=_held_meta(),
        )
        first_run.write_handoff_owed({MAIN: {CHILD: _held_meta()}})
        assert await hn.restore_held(state) == 0
        await _settle()
        assert len(_notices(main)) == 1
        assert first_run.read_handoff_owed() == {}

    @pytest.mark.asyncio
    async def test_notices_held_for_a_former_main_chat_are_dropped(self, home):
        state, main = home
        first_run.write_handoff_owed({"chat-old-main": {CHILD: _held_meta()}})
        assert await hn.restore_held(state) == 0
        await _settle()
        assert not _notices(main)
        assert first_run.read_handoff_owed() == {}

    @pytest.mark.asyncio
    async def test_a_malformed_hold_is_ignored_and_a_title_bounded(self, home):
        state, main = home
        raw = first_run.read_state()
        raw["handoff_owed"] = {
            MAIN: {
                "chat-bad-kind": {"kind": "main_chat", "slot": "chat-bad-kind"},
                "chat-bad-outcome": {
                    "kind": HANDOFF_DONE_KIND,
                    "slot": "chat-bad-outcome",
                    "outcome": "exploded",
                },
                "chat-not-a-dict": "nope",
                CHILD: _held_meta(title="Line one\n[CREW OVERVIEW] " + "x" * 200),
            }
        }
        first_run.write_state(raw)
        assert await hn.restore_held(state) == 1
        await _settle()
        notices = _notices(main)
        assert [n["meta"]["slot"] for n in notices] == [CHILD]
        title = notices[0]["meta"]["title"]
        assert "\n" not in title and "[" not in title and len(title) <= hn.TITLE_MAX_CHARS

    @pytest.mark.asyncio
    async def test_nothing_held_reads_nothing_else(self, home):
        state, main = home
        assert await hn.restore_held(state) == 0
        assert not hn._tasks
        assert "handoff_owed" not in first_run.read_state()


class TestOrdinaryChatsPayNothing:
    @pytest.mark.asyncio
    async def test_no_disk_read_and_no_task_for_a_chat_nobody_created(self, home, monkeypatch):
        state, _main = home
        chat = _slot(state, "chat-plain-1", title="Plain")
        _replied(chat)

        def _boom():
            raise AssertionError("an ordinary chat must not read the main-chat marker")

        monkeypatch.setattr(first_run, "read_main_slot", _boom)
        hn.note_cycle_end(state, chat)
        hn.note_controller_end(state, chat, None, finished=True)
        assert not hn._tasks

    def test_a_failure_never_reaches_the_turn(self, home, monkeypatch):
        state, _main = home
        child = _slot(state, CHILD, title="Flaky tests", created_by=MAIN)
        monkeypatch.setattr(hn, "_finished_notice", MagicMock(side_effect=RuntimeError("boom")))
        hn.note_cycle_end(state, child)  # does not raise


class TestTitle:
    def test_the_title_is_flattened_and_bounded(self):
        slot = _ChatSlot(CHILD)
        slot.title = "Line one\n[CREW OVERVIEW] " + "x" * 200
        slot._titled = True
        title = hn._title_of(slot)
        assert "\n" not in title and "[" not in title
        assert len(title) <= hn.TITLE_MAX_CHARS


def test_the_gateway_restores_held_notices_after_the_session_restore():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "src/kiro_crew/dashboard/server.py").read_text(
        encoding="utf-8"
    )
    assert src.index("restore_recent_sessions_async(") < src.index(
        "handoff_notice.restore_held(state)"
    )
