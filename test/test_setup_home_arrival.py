"""Home move-in promotes the copied transcript without restarting onboarding."""

from types import SimpleNamespace

import pytest

from kiro_crew import first_run
from kiro_crew import setup_cards as sc
from kiro_crew.dashboard import chat_handlers, chat_persistence
from kiro_crew.dashboard.chat_utils import slot_history_key
from kiro_crew.dashboard.setup_home_arrival import adopt_main_chat
from kiro_crew.dashboard.state import _ChatSlot
from kiro_crew.history import ConversationLog


@pytest.fixture
def home(tmp_path, monkeypatch):
    arrived = _ChatSlot("chat-9-1", title="Imported welcome", agent="kirocrew-main")
    arrived.folder_id = "imported-laptop"
    arrived.append("user", "Call me Sam")
    arrived.append("assistant", "I will remember that.")
    previous = _ChatSlot("chat-1-1", title="Welcome to Kiro Crew")
    previous.pinned = True
    slots = {s.key: s for s in (arrived, previous)}
    state = SimpleNamespace(
        _slots=slots,
        get_slot=slots.get,
        push_slots_update=lambda: None,
        conversation_log=ConversationLog(base_dir=tmp_path / "sessions"),
    )
    archived = []

    async def close(state, slot, key, *, pre_pop_check):
        pre_pop_check()
        archived.append(key)
        slots.pop(key)

    monkeypatch.setattr(chat_handlers, "close_slot", close)
    first_run.record_slot(previous.key)
    return state, arrived, previous, archived


@pytest.mark.asyncio
async def test_arrival_is_durable_main_with_history_and_progress(home):
    state, slot, previous, archived = home
    for _ in range(2):
        assert (
            await adopt_main_chat(state, slot.key, "Sam's crew", ["hello", "connect", "bogus"])
            == slot.key
        )
    assert archived == [previous.key]
    assert first_run.read_first_run_slot() == first_run.read_main_slot() == slot.key
    assert first_run.done_stages() == ["hello", "connect", "stay_on"]
    assert slot.agent == "kirocrew-main" and slot.pinned and slot.folder_id == ""
    meta, readable = state.conversation_log.get_metadata_status(slot_history_key(slot))
    assert readable and meta["title"] == "Sam's crew" and meta["pinned"] is True
    assert not meta.get("folder_id")
    rows = state.conversation_log.read_messages(slot_history_key(slot))
    assert [row["content"] for row in rows] == ["Call me Sam", "I will remember that."]


@pytest.mark.asyncio
async def test_failed_durable_save_does_not_replace_the_main_marker(home, monkeypatch):
    state, slot, previous, archived = home

    async def refused(*args, **kwargs):
        return False

    monkeypatch.setattr(chat_persistence, "save_slot_off_loop", refused)
    with pytest.raises(sc.CardRejected, match="changed"):
        await adopt_main_chat(state, slot.key, "Sam's crew", ["hello"])
    assert first_run.read_first_run_slot() == previous.key
    assert archived == []


@pytest.mark.asyncio
async def test_retry_finishes_archiving_welcome_without_replacing_arrived_chat(home):
    state, slot, previous, archived = home
    previous.task = SimpleNamespace(done=lambda: False)
    with pytest.raises(sc.CardRejected, match="still working"):
        await adopt_main_chat(state, slot.key, "Sam's crew", ["hello"])
    assert archived == []
    previous.task = None
    await adopt_main_chat(state, slot.key, "Sam's crew", ["hello"])
    assert archived == [previous.key]
    assert state.get_slot(slot.key) is slot


@pytest.mark.asyncio
async def test_an_incognito_chat_is_not_adopted(home):
    state, slot, previous, archived = home
    slot.memory_mode = "incognito"
    with pytest.raises(sc.CardRejected, match="eligible"):
        await adopt_main_chat(state, slot.key, "Sam's crew", [])
    assert first_run.read_first_run_slot() == previous.key
    assert not archived


@pytest.mark.asyncio
async def test_first_cloud_prompt_keeps_decisions_and_knows_signin_finished(home):
    from kiro_crew.context import build_session_replay
    from kiro_crew.dashboard import setup_flow, setup_transfer

    state, slot, _, _ = home
    source = sc.create_card(
        slot="chat-source", session_key="dashboard:chat-source", kind="home", payload={}
    )
    rows = [
        {
            "role": "inject",
            "content": "[Setup card result] GitHub declined. [End of setup card result]",
            "ts": "",
            "meta": {"injectKind": "setup_result"},
        },
        {
            "role": "inject",
            "content": "Choose your home",
            "ts": "",
            "meta": {"setupCard": {"id": source.id, "kind": "home"}},
        },
        {
            "role": "assistant",
            "content": "Waiting for one click to sign in.",
            "ts": "",
            "meta": {"kind": "home_signin", "opened": True},
        },
    ]
    carried, receipts = setup_transfer.remap(setup_transfer.build(rows, source.slot))
    setup_transfer.install_receipts(slot.key, receipts)
    for row in carried:
        slot.append(row["role"], row["content"], meta=row["meta"])
    await adopt_main_chat(state, slot.key, "Sam's crew", ["connect"])

    # The native-load path receives the live overview; the Tool Search path
    # also replays these durable rows. Both see the completed home state.
    overview = await setup_flow.crew_overview(state, slot)
    assert "this home; signed in to Kiro; move complete" in overview
    replay = build_session_replay(state.conversation_log, slot_history_key(slot))
    assert "GitHub declined" in replay
    assert "Your cloud home is signed in to Kiro" in replay
    assert "Waiting for one click" not in replay
