"""A new home preserves setup decisions without replaying their side effects."""

import copy
import json

import pytest

from kiro_crew import setup_cards as sc
from kiro_crew.config.paths import data_home
from kiro_crew.dashboard import setup_flow
from kiro_crew.dashboard import setup_transfer as handoff
from kiro_crew.dashboard.session_transfer import _assemble_bundle, _validate_bundle


def snapshot():
    card = sc.create_card(
        slot="chat-source",
        session_key="dashboard:chat-source",
        kind="connect",
        payload={"provider": "github"},
        private={"credential": "never carry this"},
    )
    sc.update_card(card.id, lambda c: setattr(c, "status", "declined"))
    rows = [
        {
            "role": "assistant",
            "content": "Connect a code host?",
            "ts": "t1",
            "meta": {"mid": "one"},
        },
        {
            "role": "inject",
            "content": "",
            "ts": "t2",
            "meta": {"setupCard": {"id": card.id, "kind": "connect"}},
        },
        {
            "role": "inject",
            "content": "The user declined GitHub.",
            "ts": "t3",
            "meta": {"injectKind": "setup_result"},
        },
        {
            "role": "tool",
            "content": "Setup card",
            "ts": "t4",
            "meta": {"tool_name": "setup_card", "done": True},
        },
        {
            "role": "assistant",
            "content": "Please sign in.",
            "ts": "t5",
            "meta": {"kind": "home_signin", "opened": True},
        },
    ]
    return rows, card, handoff.build(rows, card.slot)


@pytest.mark.asyncio
async def test_cards_decisions_tools_and_signin_survive_without_authority():
    source, card, data = snapshot()
    rows, receipts = handoff.remap(handoff.validate(data))
    assert [r["role"] for r in rows] == [r["role"] for r in source]
    assert rows[2:4] == source[2:4]
    assert rows[-1]["meta"]["resolved"] is True
    assert "Continue this conversation" in rows[-1]["content"]
    assert "resolved" not in source[-1]["meta"]
    assert "never carry this" not in json.dumps(data)
    assert rows[1]["meta"]["setupCard"]["id"] != card.id
    handoff.install_receipts("chat-target", receipts)
    received = sc.list_cards("chat-target")[0]
    assert received.status == "declined"
    assert received.public()["historical"] is True
    assert sc.get_card(card.id).slot == "chat-source"
    with pytest.raises(sc.CardRejected, match="record of a completed"):
        await setup_flow.decide(None, received.id, "commit", received.payload_hash, {})
    handoff.remove_receipts(receipts)
    assert not sc.list_cards("chat-target")
    assert sc.get_card(card.id) is not None


def test_home_bundle_refuses_mismatched_or_missing_receipts():
    rows, _, data = snapshot()
    bundle = {
        **_assemble_bundle(rows, "Welcome", "kirocrew-main", "laptop"),
        "bundle_version": 3,
        "home_setup": data,
    }
    valid, error = _validate_bundle(bundle)
    assert error is None and valid["home_setup"]["rows"] == rows
    mismatched = copy.deepcopy(bundle)
    mismatched["home_setup"]["rows"][0]["content"] = "Different conversation"
    assert _validate_bundle(mismatched)[1] is not None
    missing = copy.deepcopy(bundle)
    missing["home_setup"]["cards"] = []
    assert _validate_bundle(missing)[1] is not None
    assert _validate_bundle({"bundle_version": 3})[1] is not None


def test_secrets_are_redacted_from_tool_metadata_and_receipts():
    _, _, data = snapshot()
    secret = "ghp_" + "a" * 40
    data["rows"][3]["meta"]["input"] = {"token": secret}
    data["cards"][0]["payload"]["description"] = secret
    assert secret not in json.dumps(handoff.validate(data))


@pytest.mark.asyncio
async def test_preferences_replace_cloud_defaults_but_leave_runtime_config(monkeypatch):
    source = {
        "agent": {"bot_name": "Crew", "model": "auto"},
        "dashboard": {"language": "ru", "privacy_acked": True, "user_role": "developer"},
        "timezone": "America/New_York",
        "instances": {"enabled": True},
    }
    data_home().mkdir(parents=True, exist_ok=True)
    (data_home() / "config.json").write_text(json.dumps(source))
    sc.persona_path("SOUL").parent.mkdir(parents=True, exist_ok=True)
    sc.persona_path("SOUL").write_text("Keep replies direct.\n")
    preferences = handoff.read_preferences()
    cloud = {
        "agent": {"model": "cloud-default"},
        "dashboard": {"privacy_acked": False},
        "instances": {"enabled": False},
        "security": {"sandbox": True},
    }

    async def update(mutate):
        mutate(cloud)

    monkeypatch.setattr(setup_flow, "_update_config", update)
    sc.persona_path("SOUL").write_text("Cloud default\n")
    await handoff.apply_preferences(preferences)
    assert cloud["agent"] == {"bot_name": "Crew", "model": "cloud-default"}
    assert cloud["dashboard"] == {
        "privacy_acked": False,
        "bot_name": "Crew",
        "language": "ru",
        "user_role": "developer",
    }
    assert cloud["timezone"] == "America/New_York"
    assert cloud["instances"] == {"enabled": False}
    assert cloud["security"] == {"sandbox": True}
    assert sc.persona_path("SOUL").read_text() == "Keep replies direct.\n"


def test_only_latest_transferred_home_receipt_is_completed():
    card = sc.create_card(
        slot="chat-source", session_key="dashboard:chat-source", kind="home", payload={}
    )
    data = handoff.build(
        [{"role": "inject", "content": "", "ts": "", "meta": {"setupCard": {"id": card.id}}}],
        card.slot,
    )
    _, receipts = handoff.remap(data)
    handoff.install_receipts("chat-target", receipts)
    assert sc.list_cards("chat-target")[0].status == "expired"
    for _ in range(2):
        handoff.complete_receipts("chat-target", {"home": {"name": "Cloud"}})
    arrived = sc.list_cards("chat-target")[0]
    assert arrived.status == "committed" and arrived.outcome["arrived"]
    assert sc.get_card(card.id).status == "pending"
