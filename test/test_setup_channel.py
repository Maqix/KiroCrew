"""The channel setup card: the bot token typed into the card, then ``/pair``.

Pins what ``dashboard/setup_channel.py`` promises:

* SC2 — the token reaches the credential file (``TELEGRAM_BOT_TOKEN`` in
  ``.env``, where the Telegram channel reads it) and nowhere else: not the card,
  the store, an event, the transcript, a turn or a log record;
* the pairing code lives in process memory only: never in the store, the
  transcript or a turn the model reads;
* a matching ``/pair`` adds the sender to ``telegram.allowed_user_ids`` and
  commits the card; a wrong or expired code adds nobody; the card expires;
* the Telegram transport answers ``/pair`` before authorization, in a DM only,
  and a consumed message never reaches the agent.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest

from kiro_crew import setup_cards as sc
from kiro_crew.config.loader import config_path, env_path
from kiro_crew.config.paths import data_home
from kiro_crew.dashboard import setup_channel, setup_flow
from kiro_crew.dashboard.handlers import messaging
from kiro_crew.telegram.client import TelegramInbound
from kiro_crew.telegram.commands import parse_pair_command
from kiro_crew.telegram.transport import TelegramTransport

SENTINEL_TOKEN = "110201543:AAHsentinelTokenValue5f1c9e2b7a4d"
OWNER_ID = "424242"


class FakeSlot:
    def __init__(self, key: str = "chat-1-1") -> None:
        self.key = key
        self.messages: list[tuple[str, str, dict | None]] = []
        self.running = False
        self._in_stage_execution = False
        self.task = None

    def append(self, role, content, cls="", ts="", *, broadcast=True, meta=None):
        self.messages.append((role, content, meta))


class FakeState:
    def __init__(self) -> None:
        self.slots = {"chat-1-1": FakeSlot()}
        self.events: list[tuple[str, Any]] = []
        self.restart_channel: Any = None

    def get_slot(self, key):
        return self.slots.get(key)

    def broadcast_ws_owners(self, msg_type, data):
        self.events.append((msg_type, data))

    def push_slots_update(self, **_):
        pass


@pytest.fixture
def state():
    return FakeState()


@pytest.fixture
def dispatched(monkeypatch):
    """Record envelope turns instead of starting real model turns."""
    calls: list[tuple[str, str, str]] = []

    async def _fake(state, slot, text, inject_kind):
        calls.append((slot.key, inject_kind, text))

    monkeypatch.setattr(setup_flow, "_dispatch_envelope_turn", _fake)
    return calls


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Permit governance, never reach Telegram, and restore what the commit touches."""
    monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: None)

    async def _accept(token):
        return None

    monkeypatch.setattr(messaging, "_validate_telegram_token", _accept)
    # The commit syncs os.environ the way the Settings save does; set-then-delete
    # records the key's absence so teardown removes what the commit wrote.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "unset")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    monkeypatch.setattr(setup_channel, "_pairings", {})
    monkeypatch.setattr(setup_channel, "_tasks", set())


@contextlib.asynccontextmanager
async def _scope() -> AsyncIterator[None]:
    """Cancel the module's watchers before the test's loop closes."""
    try:
        yield
    finally:
        tasks = list(setup_channel._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=5)


async def _commit(state, token: str = SENTINEL_TOKEN) -> sc.SetupCard:
    out = await setup_flow.propose(
        state,
        state.slots["chat-1-1"],
        "dashboard:chat-1-1",
        {"kind": "channel", "channel": "telegram"},
        producer_is_user_facing=True,
    )
    assert out.startswith("Setup card shown"), out
    (card,) = sc.list_cards("chat-1-1")
    return await setup_flow.decide(state, card.id, "commit", card.payload_hash, {"token": token})


def _stored(card_id: str) -> sc.SetupCard:
    card = sc.get_card(card_id)
    assert card is not None
    return card


def _telegram_section() -> dict:
    path = config_path()
    return json.loads(path.read_text(encoding="utf-8")).get("telegram", {}) if path.exists() else {}


async def _until(predicate, timeout: float = 5.0) -> None:
    async def _poll():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout)


def _files_holding(needle: str) -> list[Path]:
    return [p for p in data_home().rglob("*") if p.is_file() and needle.encode() in p.read_bytes()]


class TestCommit:
    @pytest.mark.asyncio
    async def test_s2_the_bot_token_reaches_the_credential_file_and_nowhere_else(
        self, state, dispatched, caplog
    ):
        caplog.set_level(logging.DEBUG)
        async with _scope():
            decided = await _commit(state)
            assert decided.status == sc.STATUS_WAITING
            assert f"TELEGRAM_BOT_TOKEN={SENTINEL_TOKEN}" in env_path().read_text(encoding="utf-8")
            assert _telegram_section()["enabled"] is True
            assert _files_holding(SENTINEL_TOKEN) == [env_path()]
            assert SENTINEL_TOKEN not in json.dumps(decided.public())
            assert SENTINEL_TOKEN not in json.dumps(state.events)
            assert all(SENTINEL_TOKEN not in text for _, _, text in dispatched)
            assert all(SENTINEL_TOKEN not in str(m) for m in state.slots["chat-1-1"].messages)
            assert SENTINEL_TOKEN not in caplog.text

    @pytest.mark.asyncio
    async def test_the_pairing_code_is_shown_to_the_owner_and_kept_off_disk(
        self, state, dispatched, caplog
    ):
        caplog.set_level(logging.DEBUG)
        async with _scope():
            decided = await _commit(state)
            code = decided.outcome["pair_code"]
            assert re.fullmatch(r"\d{4}", code)
            # The owner sees it: the decide response, the owner-only event, the GET view.
            assert state.events[-1][1]["card"]["outcome"]["pair_code"] == code
            assert setup_channel.owner_view(_stored(decided.id))["outcome"]["pair_code"] == code
            # Nothing the agent can read holds it.
            stored = _stored(decided.id)
            assert "pair_code" not in (stored.outcome or {})
            assert "pair_code" not in json.dumps(stored.private)
            assert _files_holding('"pair_code"') == []
            assert all(code not in text for _, _, text in dispatched)
            assert all(code not in str(m) for m in state.slots["chat-1-1"].messages)
            assert f"/pair {code}" not in caplog.text

    @pytest.mark.asyncio
    async def test_an_empty_or_malformed_token_leaves_the_card_pending(self, state, dispatched):
        async with _scope():
            decided = await _commit(state, token="  ")
            assert decided.status == sc.STATUS_PENDING
            assert decided.error["code"] == "channel_token_empty"
            (card,) = sc.list_cards("chat-1-1")
            decided = await setup_flow.decide(
                state, card.id, "commit", card.payload_hash, {"token": "not a token"}
            )
            assert decided.status == sc.STATUS_PENDING
            assert decided.error["code"] == "channel_token_invalid"
            assert not env_path().exists() or "TELEGRAM_BOT_TOKEN" not in env_path().read_text()
            assert setup_channel._pairings == {}
            assert dispatched == []

    @pytest.mark.asyncio
    async def test_a_token_telegram_rejects_is_not_stored(self, state, dispatched, monkeypatch):
        async def _reject(token):
            return "Unauthorized"

        monkeypatch.setattr(messaging, "_validate_telegram_token", _reject)
        async with _scope():
            decided = await _commit(state)
            assert decided.status == sc.STATUS_PENDING
            assert decided.error["code"] == "channel_token_rejected"
            assert SENTINEL_TOKEN not in decided.error["message"]
            assert _files_holding(SENTINEL_TOKEN) == []

    @pytest.mark.asyncio
    async def test_offline_stores_the_token_anyway(self, state, dispatched, monkeypatch, caplog):
        async def _offline(token):
            raise OSError(f"cannot reach https://api.telegram.org/bot{token}/getMe")

        monkeypatch.setattr(messaging, "_validate_telegram_token", _offline)
        caplog.set_level(logging.DEBUG)
        async with _scope():
            decided = await _commit(state)
            assert decided.status == sc.STATUS_WAITING
            assert SENTINEL_TOKEN not in caplog.text

    @pytest.mark.asyncio
    async def test_enabling_the_channel_leaves_the_restart_to_the_config_watcher(
        self, state, dispatched
    ):
        calls: list[str] = []

        async def _restart(channel_type):
            calls.append(channel_type)

        state.restart_channel = _restart
        async with _scope():
            await _commit(state)
            await asyncio.sleep(0.05)
        assert calls == []

    @pytest.mark.asyncio
    async def test_a_token_swapped_under_an_enabled_channel_restarts_it(self, state, dispatched):
        config_path().write_text(json.dumps({"telegram": {"enabled": True}}), encoding="utf-8")
        calls: list[str] = []

        async def _restart(channel_type):
            calls.append(channel_type)

        state.restart_channel = _restart
        async with _scope():
            await _commit(state)
            await _until(lambda: calls == ["telegram"])

    @pytest.mark.asyncio
    async def test_a_legacy_config_token_is_purged(self, state, dispatched):
        config_path().write_text(
            json.dumps({"telegram": {"enabled": True, "bot_token": "1:legacyLegacyLegacy"}}),
            encoding="utf-8",
        )
        async with _scope():
            await _commit(state)
        assert "bot_token" not in _telegram_section()


class TestPair:
    @pytest.mark.asyncio
    async def test_a_matching_code_allowlists_the_sender_and_commits_the_card(
        self, state, dispatched
    ):
        async with _scope():
            decided = await _commit(state)
            code = decided.outcome["pair_code"]
            reply = await setup_channel.telegram_pair_attempt(OWNER_ID, "@owner", code)
            assert reply == setup_channel._REPLY_PAIRED
            assert _telegram_section()["allowed_user_ids"] == [int(OWNER_ID)]
            card = _stored(decided.id)
            assert card.status == sc.STATUS_COMMITTED
            assert card.outcome == {"channel": "telegram", "paired": True, "username": "@owner"}
            assert state.events[-1][1]["card"]["status"] == sc.STATUS_COMMITTED
            ((_, kind, text),) = dispatched
            assert kind == "setup_result" and "@owner" in text and code not in text
            # One-time: the same code pairs nobody else.
            assert await setup_channel.telegram_pair_attempt("777", "", code) is None
            assert _telegram_section()["allowed_user_ids"] == [int(OWNER_ID)]

    @pytest.mark.asyncio
    async def test_pairing_keeps_the_ids_already_allowed(self, state, dispatched):
        config_path().write_text(
            json.dumps({"telegram": {"allowed_user_ids": [1001]}}), encoding="utf-8"
        )
        async with _scope():
            decided = await _commit(state)
            await setup_channel.telegram_pair_attempt(OWNER_ID, "", decided.outcome["pair_code"])
        assert _telegram_section()["allowed_user_ids"] == [1001, int(OWNER_ID)]

    @pytest.mark.asyncio
    async def test_a_wrong_code_adds_nobody_and_the_last_one_closes_the_pairing(
        self, state, dispatched
    ):
        async with _scope():
            decided = await _commit(state)
            code = decided.outcome["pair_code"]
            wrong = f"{(int(code) + 1) % 10000:04d}"
            for _ in range(setup_channel.PAIR_MAX_WRONG - 1):
                reply = await setup_channel.telegram_pair_attempt("666", "", wrong)
                assert reply == setup_channel._REPLY_WRONG
                assert _stored(decided.id).status == sc.STATUS_WAITING
            assert "allowed_user_ids" not in _telegram_section()
            reply = await setup_channel.telegram_pair_attempt("666", "", wrong)
            assert reply == setup_channel._REPLY_CLOSED
            card = _stored(decided.id)
            assert card.status == sc.STATUS_FAILED
            assert card.error["code"] == "pair_attempts"
            # Closed: the right code now pairs nobody.
            assert await setup_channel.telegram_pair_attempt(OWNER_ID, "", code) is None
            assert "allowed_user_ids" not in _telegram_section()

    @pytest.mark.asyncio
    async def test_an_expired_code_pairs_nobody_and_the_card_expires(
        self, state, dispatched, monkeypatch
    ):
        monkeypatch.setattr(setup_channel, "PAIR_WAIT_SECS", 0.05)
        async with _scope():
            decided = await _commit(state)
            code = decided.outcome["pair_code"]
            await _until(lambda: _stored(decided.id).status == sc.STATUS_EXPIRED)
            assert _stored(decided.id).error["code"] == "pair_timeout"
            assert await setup_channel.telegram_pair_attempt(OWNER_ID, "", code) is None
            assert "allowed_user_ids" not in _telegram_section()
            assert "pair_code" not in setup_channel.owner_view(_stored(decided.id))["outcome"]
            assert [kind for _, kind, _ in dispatched] == ["setup_result"]

    @pytest.mark.asyncio
    async def test_a_code_past_its_deadline_is_refused_before_the_watcher_runs(
        self, state, dispatched
    ):
        async with _scope():
            decided = await _commit(state)
            setup_channel._pairings["telegram"].deadline = 0.0
            reply = await setup_channel.telegram_pair_attempt(
                OWNER_ID, "", decided.outcome["pair_code"]
            )
            assert reply is None
            assert "allowed_user_ids" not in _telegram_section()

    @pytest.mark.asyncio
    async def test_no_live_pairing_answers_nothing(self):
        assert await setup_channel.telegram_pair_attempt(OWNER_ID, "", "1234") is None

    @pytest.mark.asyncio
    async def test_a_newer_card_replaces_the_older_code(self, state, dispatched):
        async with _scope():
            first = await _commit(state)
            out = await setup_flow.propose(
                state,
                state.slots["chat-1-1"],
                "dashboard:chat-1-1",
                {"kind": "channel", "channel": "telegram"},
                producer_is_user_facing=True,
            )
            assert out.startswith("Setup card shown"), out
            second = next(c for c in sc.list_cards("chat-1-1") if c.status == sc.STATUS_PENDING)
            second = await setup_flow.decide(
                state, second.id, "commit", second.payload_hash, {"token": SENTINEL_TOKEN}
            )
            await _until(lambda: _stored(first.id).status == sc.STATUS_EXPIRED)
            assert _stored(first.id).error["code"] == "pair_superseded"
            old = first.outcome["pair_code"]
            if old != second.outcome["pair_code"]:
                assert await setup_channel.telegram_pair_attempt(OWNER_ID, "", old) == (
                    setup_channel._REPLY_WRONG
                )


class TestAllowListWrite:
    @pytest.mark.asyncio
    async def test_a_stored_value_that_is_not_a_list_is_left_alone(self):
        config_path().write_text(
            json.dumps({"telegram": {"allowed_user_ids": "123"}}), encoding="utf-8"
        )
        with pytest.raises(ValueError):
            await messaging.add_telegram_allowed_user(int(OWNER_ID))
        assert _telegram_section()["allowed_user_ids"] == "123"

    @pytest.mark.asyncio
    async def test_an_id_already_allowed_is_not_added_twice(self):
        config_path().write_text(
            json.dumps({"telegram": {"allowed_user_ids": ["424242"]}}), encoding="utf-8"
        )
        assert await messaging.add_telegram_allowed_user(int(OWNER_ID)) is False
        assert _telegram_section()["allowed_user_ids"] == ["424242"]


class _Client:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **_):
        self.sent.append((chat_id, text))
        return 1


def _inbound(text: str, *, user_id: int = 424242, chat_type: str = "private") -> TelegramInbound:
    return TelegramInbound(
        chat_id=user_id if chat_type == "private" else -1001,
        user_id=user_id,
        username="owner",
        text=text,
        message_id=7,
        chat_type=chat_type,
    )


class TestTransport:
    def _transport(self, *, allowed=(), reply: str | None = "Paired."):
        client = _Client()
        dispatched: list[Any] = []
        seen: list[tuple[str, str, str]] = []

        async def _dispatch(msg):
            dispatched.append(msg)

        async def _pair(user_id, handle, code):
            seen.append((user_id, handle, code))
            return reply

        transport = TelegramTransport(
            client,  # type: ignore[arg-type]
            allowed_user_ids=allowed,
            dispatch=_dispatch,
        )
        transport.pair_handler = _pair
        return transport, SimpleNamespace(client=client, dispatched=dispatched, seen=seen)

    @pytest.mark.asyncio
    async def test_an_unlisted_sender_reaches_the_pair_handler_and_never_the_agent(self):
        transport, rec = self._transport()
        await transport.receive(_inbound("/pair 4821"))
        assert rec.seen == [("424242", "@owner", "4821")]
        assert rec.client.sent == [(424242, "Paired.")]
        assert rec.dispatched == []

    @pytest.mark.asyncio
    async def test_with_no_live_pairing_the_message_takes_the_ordinary_path(self):
        transport, rec = self._transport(reply=None)
        await transport.receive(_inbound("/pair 4821"))
        assert rec.client.sent == []
        assert rec.dispatched == []  # unlisted: deny-by-default drops it

        transport, rec = self._transport(allowed=[424242], reply=None)
        await transport.receive(_inbound("/pair 4821"))
        assert [m.text for m in rec.dispatched] == ["/pair 4821"]

    @pytest.mark.asyncio
    async def test_a_group_message_is_never_read_as_a_pairing(self):
        transport, rec = self._transport()
        await transport.receive(_inbound("/pair 4821", chat_type="group"))
        assert rec.seen == [] and rec.client.sent == []

    @pytest.mark.asyncio
    async def test_ordinary_messages_do_not_reach_the_pair_handler(self):
        transport, rec = self._transport(allowed=[424242])
        await transport.receive(_inbound("hello"))
        assert rec.seen == []
        assert [m.text for m in rec.dispatched] == ["hello"]


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("/pair 4821", "4821"),
        ("  /pair   4821  ", "4821"),
        ("/PAIR 4821", "4821"),
        ("/pair@my_bot 4821", "4821"),
        ("/pair", None),
        ("/pair abcd", None),
        ("/pair 4821 please", None),
        ("pair 4821", None),
        ("/pairing 4821", None),
    ],
)
def test_parse_pair_command(text, code):
    assert parse_pair_command(text) == code
