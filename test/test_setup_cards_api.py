"""HTTP surface of setup cards: owner-only, and the decide route is the commit path."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from aiohttp import StreamReader, web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from kiro_crew import first_run
from kiro_crew import setup_cards as sc
from kiro_crew.dashboard import setup_flow
from kiro_crew.dashboard.handlers import setup_cards as handlers


class _State:
    def __init__(self) -> None:
        self.events: list = []

    def get_slot(self, key):
        return None

    def broadcast_ws_owners(self, msg_type, data):
        self.events.append((msg_type, data))


def _app(owner: bool) -> web.Application:
    app = web.Application()
    app["state"] = _State()
    handlers.register_routes(app)
    return app


@pytest.fixture
def as_owner(monkeypatch):
    async def _gate(request, operation):
        return None

    monkeypatch.setattr(handlers, "require_owner_dashboard_request", _gate)


@pytest.fixture
def as_stranger(monkeypatch):
    async def _gate(request, operation):
        return web.json_response({"error": "owner only", "code": "owner_only"}, status=403)

    monkeypatch.setattr(handlers, "require_owner_dashboard_request", _gate)


def _card() -> sc.SetupCard:
    return sc.create_card(
        slot="chat-1-1",
        session_key="dashboard:chat-1-1",
        kind=sc.KIND_PROFILE,
        payload={"fields": {"bot_name": "Nova"}},
    )


@pytest.mark.asyncio
async def test_a_stranger_can_neither_read_nor_decide(as_stranger):
    card = _card()
    async with TestClient(TestServer(_app(owner=False))) as client:
        r = await client.get("/api/setup/cards", params={"slot": "chat-1-1"})
        assert r.status == 403
        r = await client.get(f"/api/setup/cards/{card.id}")
        assert r.status == 403
        r = await client.post(
            f"/api/setup/cards/{card.id}/decide",
            json={"decision": "commit", "hash": card.payload_hash},
        )
        assert r.status == 403
    assert sc.get_card(card.id).status == sc.STATUS_PENDING


@pytest.mark.asyncio
async def test_owner_lists_and_reads_cards(as_owner):
    card = _card()
    async with TestClient(TestServer(_app(owner=True))) as client:
        r = await client.get("/api/setup/cards", params={"slot": "chat-1-1"})
        assert r.status == 200
        body = await r.json()
        assert [c["id"] for c in body["cards"]] == [card.id]
        r = await client.get(f"/api/setup/cards/{card.id}")
        assert (await r.json())["hash"] == card.payload_hash
        r = await client.get("/api/setup/cards/sc-0000000000000000")
        assert r.status == 404


@pytest.mark.asyncio
async def test_decide_maps_refusals_to_statuses(as_owner, monkeypatch):
    monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: None)

    async def _no_report(state, card):
        pass

    monkeypatch.setattr(setup_flow, "_report", _no_report)
    card = _card()
    async with TestClient(TestServer(_app(owner=True))) as client:
        r = await client.post(
            f"/api/setup/cards/{card.id}/decide", json={"decision": "commit", "hash": "0" * 64}
        )
        assert r.status == 409 and (await r.json())["code"] == "card_hash_mismatch"
        r = await client.post(
            f"/api/setup/cards/{card.id}/decide",
            json={"decision": "nope", "hash": card.payload_hash},
        )
        assert r.status == 400
        r = await client.post(
            f"/api/setup/cards/{card.id}/decide",
            json={"decision": "commit", "hash": card.payload_hash},
        )
        assert r.status == 200 and (await r.json())["status"] == sc.STATUS_COMMITTED
        r = await client.post(
            f"/api/setup/cards/{card.id}/decide",
            json={"decision": "commit", "hash": card.payload_hash},
        )
        assert r.status == 409 and (await r.json())["code"] == "card_not_pending"


@pytest.mark.asyncio
async def test_first_run_reports_the_recorded_slot(as_owner):
    first_run.record_slot("chat-7-7")
    async with TestClient(TestServer(_app(owner=True))) as client:
        r = await client.get("/api/setup/first-run")
        assert await r.json() == {"slot": "chat-7-7", "active": False}


class _Slot:
    def __init__(self, key: str) -> None:
        self.key = key
        self.pinned = False
        self.channel_origin = None
        self.messages: list = []

    def append(self, role, content, css="", meta=None):
        self.messages.append((role, content, meta))


class _StateWithSlots(_State):
    def __init__(self, *keys: str) -> None:
        super().__init__()
        self.slots = {k: _Slot(k) for k in keys}
        self.pushed = 0

    def get_slot(self, key):
        return self.slots.get(key)

    def push_slots_update(self, **_):
        self.pushed += 1


@pytest.mark.asyncio
async def test_the_owner_makes_a_chat_the_main_chat(as_owner):
    app = _app(owner=True)
    app["state"] = _StateWithSlots("chat-3-3", "cron-9-9")
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/api/setup/main-chat", json={"slot": "chat-3-3"})
        assert r.status == 200 and await r.json() == {"main_slot": "chat-3-3"}
        r = await client.post("/api/setup/main-chat", json={"slot": "cron-9-9"})
        assert r.status == 409 and (await r.json())["code"] == "slot_not_eligible"
        r = await client.post("/api/setup/main-chat", json={"slot": "chat-0-0"})
        assert r.status == 404
    assert first_run.read_main_slot() == "chat-3-3"
    slot = app["state"].slots["chat-3-3"]
    assert slot.pinned and slot.messages[-1][2] == {"kind": "main_chat"}


@pytest.mark.asyncio
async def test_a_stranger_cannot_move_the_main_chat(as_stranger):
    async with TestClient(TestServer(_app(owner=False))) as client:
        r = await client.post("/api/setup/main-chat", json={"slot": "chat-3-3"})
        assert r.status == 403
    assert first_run.read_main_slot() is None


def _arrival_request(body):
    raw = json.dumps(body).encode()
    payload = StreamReader(Mock(_reading_paused=False), limit=4096)
    payload.feed_data(raw)
    payload.feed_eof()
    return make_mocked_request(
        "POST",
        "/api/setup/home-arrival",
        app=_app(owner=True),
        payload=payload,
        headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
    )


@pytest.mark.asyncio
async def test_home_arrival_is_owner_only(as_stranger):
    response = await handlers.api_setup_home_arrival(_arrival_request({"slot": "chat-9-1"}))
    assert response.status == 403


@pytest.mark.asyncio
async def test_home_arrival_validates_input_and_returns_adopted_chat(as_owner, monkeypatch):
    from kiro_crew.dashboard import setup_home_arrival

    calls = []

    async def adopt(state, slot, title, stages, **kwargs):
        calls.append((slot, title, stages))
        assert kwargs["preferences"] == {"fields": {}, "persona": {}}
        return slot

    monkeypatch.setattr(setup_home_arrival, "adopt_main_chat", adopt)
    response = await handlers.api_setup_home_arrival(
        _arrival_request({"slot": "chat-9-1", "title": [], "stages": []})
    )
    assert response.status == 400 and calls == []
    response = await handlers.api_setup_home_arrival(
        _arrival_request({"slot": "chat-9-1", "title": "Welcome", "stages": ["hello"]})
    )
    assert response.status == 200
    assert json.loads(response.body) == {"main_slot": "chat-9-1"}
    assert calls == [("chat-9-1", "Welcome", ["hello"])]
    response = await handlers.api_setup_home_arrival(
        _arrival_request({"title": "x" * (1024 * 1024)})
    )
    assert response.status == 413 and len(calls) == 1
