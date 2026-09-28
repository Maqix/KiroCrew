"""The first week's daily tips in the main chat (RFC one-chat first run §5.8)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from kiro_crew import first_run
from kiro_crew.dashboard import first_week as fw

DAY = 86400.0
_NO_FACTS = {
    "connected": False,
    "channel": False,
    "service": False,
    "moved": False,
    "jobs": 1,
    "soul": False,
}


def _daytime(ts: float) -> float:
    """*ts* moved to 10:00 local time on the same day."""
    lt = time.localtime(ts)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 10, 0, 0, 0, 0, -1))


def _due(**over):
    graduated = _daytime(time.time()) - 3 * DAY
    kw = dict(
        now=graduated + 2 * DAY,
        graduated=graduated,
        week={},
        last_user=graduated + 2 * DAY - 3600,
        facts=dict(_NO_FACTS),
    )
    kw.update(over)
    return fw.next_tip(**kw)


class TestNextTip:
    def test_the_first_applicable_tip_on_an_active_day(self):
        assert _due().id == "connect"

    def test_shown_and_satisfied_tips_are_skipped(self):
        tip = _due(week={"shown": ["connect"]}, facts={**_NO_FACTS, "service": True})
        assert tip.id == "channel"

    @pytest.mark.parametrize(
        "over",
        [
            {"graduated": None},
            {"week": {"stopped": True}},
            {"last_user": None},
        ],
    )
    def test_nothing_without_graduation_when_stopped_or_idle(self, over):
        assert _due(**over) is None

    def test_not_on_the_first_day_nor_after_the_week(self):
        g = _daytime(time.time()) - 10 * DAY
        assert _due(graduated=g, now=g + 0.5 * DAY, last_user=g + 0.4 * DAY) is None
        now = g + (fw.TIP_DAYS + 1) * DAY
        assert _due(graduated=g, now=now, last_user=now - 60) is None

    def test_at_most_one_a_day_and_only_in_the_daytime(self):
        base = _daytime(time.time()) - 3 * DAY
        now = base + 2 * DAY
        assert _due(now=now, week={"last_ts": now - 3600}) is None
        night = now - 8 * 3600  # 02:00
        assert _due(now=night, last_user=night - 60) is None

    def test_a_user_who_has_not_used_the_chat_today_gets_nothing(self):
        base = _daytime(time.time()) - 3 * DAY
        now = base + 2 * DAY
        assert _due(now=now, last_user=now - fw.ACTIVE_WITHIN_SECS - 1) is None


class _Slot:
    def __init__(self, key: str, user_ts: float | None) -> None:
        self.key = key
        self.messages: list = []
        if user_ts is not None:
            iso = datetime.fromtimestamp(user_ts, tz=timezone.utc).isoformat()
            self.messages.append({"role": "user", "content": "hi", "ts": iso})

    def append(self, role, content, cls="", meta=None):
        self.messages.append({"role": role, "content": content, "meta": meta})


def _state(slot):
    return SimpleNamespace(
        get_slot=lambda k: slot if k == slot.key else None,
        crons=SimpleNamespace(list_jobs=lambda: []),
        push_slots_update=lambda **_: None,
    )


def _graduate(slot_key: str, at: float) -> None:
    first_run.record_slot(slot_key)
    first_run.record_main(slot_key)
    state = first_run.read_state()
    state["stages"] = {"job_kept": at}
    first_run.write_state(state)


class TestTick:
    @pytest.mark.asyncio
    async def test_posts_one_tip_as_a_notice_and_remembers_it(self, monkeypatch):
        from kiro_crew.dashboard import setup_flow

        monkeypatch.setattr(setup_flow, "_service_payload", lambda: {"installed": False})
        graduated = _daytime(time.time()) - 3 * DAY
        now = graduated + 2 * DAY
        slot = _Slot("chat-1-1", now - 600)
        _graduate(slot.key, graduated)
        assert await fw.tick(_state(slot), now=now) == "connect"
        notice = slot.messages[-1]
        assert notice["meta"] == {"kind": "first_week_tip", "tip": "connect"}
        assert "no more tips" in notice["content"]
        assert await fw.tick(_state(slot), now=now + 60) is None  # one a day

    @pytest.mark.asyncio
    async def test_two_unanswered_tips_end_the_week(self, monkeypatch):
        from kiro_crew.dashboard import setup_flow

        monkeypatch.setattr(setup_flow, "_service_payload", lambda: {"installed": False})
        graduated = _daytime(time.time()) - 5 * DAY
        slot = _Slot("chat-1-1", graduated + DAY + 60)
        _graduate(slot.key, graduated)
        # Day 2: the user was active the day before, so a tip posts; they never answer.
        first_run_state = first_run.read_state()
        first_run_state["first_week"] = {
            "shown": ["connect"],
            "last_ts": graduated + DAY + 120,
            "unanswered": 1,
        }
        first_run.write_state(first_run_state)
        slot.messages[0]["ts"] = datetime.fromtimestamp(
            graduated + DAY + 60, tz=timezone.utc
        ).isoformat()
        now = graduated + 2 * DAY
        monkeypatch.setattr(fw, "ACTIVE_WITHIN_SECS", 10 * DAY)
        assert await fw.tick(_state(slot), now=now) is None
        assert first_run.read_state()["first_week"]["stopped"] is True

    def test_the_user_can_stop_them_in_the_main_chat_only(self):
        first_run.record_main("chat-1-1")
        assert fw.note_user_message("chat-2-2", "no more tips") is False
        assert fw.note_user_message("chat-1-1", "thanks, that's all") is False
        assert fw.note_user_message("chat-1-1", "No more tips please") is True
        assert first_run.read_state()["first_week"]["stopped"] is True

    @pytest.mark.asyncio
    async def test_an_install_without_a_first_run_never_starts_the_loop(self):
        await fw.run(_state(_Slot("chat-1-1", None)))  # returns at once
