"""The home card's "Sign in to AWS": ``aws login`` run from the card, never a terminal.

No test here runs the AWS CLI or reaches AWS: the child is a fake process and the
sign-in check is a fake answer. Pins:

* a sign-in that lands returns the card to ``pending`` with ``aws_signed_in`` in its
  outcome while the payload (and its hash) stays what the owner saw;
* a sign-in not finished in time, or cancelled, stops the child and leaves the card
  ``pending``;
* a gateway the owner's browser is not on is refused with the terminal command
  that works there (``aws login --remote``), and nothing is spawned;
* SC4: the child's streams are closed, and nothing the CLI or AWS prints beyond the
  account's last four digits reaches the card, the store or a log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from kiro_crew import setup_cards as sc
from kiro_crew.cloud import local_signin
from kiro_crew.config.paths import data_home
from kiro_crew.dashboard import setup_aws_signin as signin
from kiro_crew.dashboard import setup_flow

SENTINEL = "AROASENTINEL5f1c9e2b7a4d"
#: The real helpers, captured before any fixture replaces them.
_REAL_CLI_PROBLEM = signin._cli_problem
_REAL_BROWSER_IS_HERE = signin.browser_is_here
_REAL_SIGNED_IN_ACCOUNT = signin._signed_in_account


class FakeState:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def get_slot(self, key):
        return None

    def broadcast_ws_owners(self, msg_type, data):
        self.events.append((msg_type, data))


class FakeProc:
    """A stand-in ``aws login`` that runs until told to exit or stopped."""

    pid = 4242

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return self.returncode


@pytest.fixture
def state():
    return FakeState()


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: None)
    monkeypatch.setattr(signin, "_POLL_SECS", 0)
    monkeypatch.setattr(signin, "_EXIT_GRACE_SECS", 0)
    monkeypatch.setattr(signin, "_cli_problem", lambda: None)
    monkeypatch.setattr(signin, "browser_is_here", lambda same_machine: same_machine)
    yield
    for live in list(signin._live.values()):
        if live.task is not None:
            live.task.cancel()
    signin._live.clear()


@pytest.fixture
def aws(monkeypatch):
    """The AWS answer (``None`` = not signed in) and every spawn the card makes."""
    world: dict[str, Any] = {"account": None, "spawned": [], "procs": []}

    def _account(profile):
        return world["account"]

    def _spawn(profile, region):
        proc = FakeProc()
        world["spawned"].append((profile, region))
        world["procs"].append(proc)
        return proc

    monkeypatch.setattr(signin, "_signed_in_account", _account)
    monkeypatch.setattr(signin, "_spawn_login", _spawn)
    return world


def _home_card(monkeypatch, *, profile: str = "work", region: str = "eu-west-1") -> sc.SetupCard:
    from kiro_crew.cloud import iam

    monkeypatch.setattr(iam, "reachability_check", lambda p, r: {"reachable": False})
    monkeypatch.setattr(local_signin, "kiro_signs_in_with_builder_id", lambda: False)
    settings = sc.build_home({"region": region, "profile": profile})
    payload, private = setup_flow._home_payload(settings)
    assert payload["aws_signed_in"] is payload["simulated"]
    return sc.create_card(
        slot="chat-1-1",
        session_key="dashboard:chat-1-1",
        kind=sc.KIND_HOME,
        payload=payload,
        private=private,
    )


async def _sign_in(state, card, *, same_machine=True, **input_):
    return await setup_flow.decide(
        state, card.id, "aws_signin", card.payload_hash, input_, same_machine=same_machine
    )


async def _watcher_done(card_id: str) -> None:
    task = signin._live[card_id].task
    assert task is not None
    await asyncio.wait_for(task, 5)


class TestSignIn:
    @pytest.mark.asyncio
    async def test_a_landed_sign_in_returns_the_card_ready_to_build(self, state, aws, monkeypatch):
        card = _home_card(monkeypatch)
        waiting = await _sign_in(state, card)
        assert waiting.status == sc.STATUS_WAITING
        assert waiting.outcome["aws_signin"]["state"] == "waiting"
        assert waiting.outcome["aws_signin"]["expires_ts"] > time.time()
        assert aws["spawned"] == [("work", "eu-west-1")]
        aws["account"] = "…9012"
        aws["procs"][0].returncode = 0
        await _watcher_done(card.id)
        ready = sc.get_card(card.id)
        assert ready.status == sc.STATUS_PENDING and ready.error is None
        assert ready.outcome["aws_signed_in"] is True
        assert ready.outcome["aws_account"] == "…9012"
        # The payload the owner approved is unchanged, so Build takes the same hash.
        assert ready.payload_hash == card.payload_hash
        assert ready.payload["aws_signed_in"] is False
        assert "aws_signin_run" not in ready.private
        assert not signin._live
        assert state.events[-1][1]["card"]["outcome"]["aws_signed_in"] is True

    @pytest.mark.asyncio
    async def test_a_machine_already_signed_in_spawns_nothing(self, state, aws, monkeypatch):
        card = _home_card(monkeypatch)
        aws["account"] = "…0001"
        done = await _sign_in(state, card)
        assert done.status == sc.STATUS_PENDING
        assert done.outcome["aws_signed_in"] is True
        assert aws["spawned"] == []

    @pytest.mark.asyncio
    async def test_a_sign_in_not_finished_in_time_stops_the_child(self, state, aws, monkeypatch):
        monkeypatch.setattr(signin, "SIGNIN_WAIT_SECS", 0)
        card = _home_card(monkeypatch)
        await _sign_in(state, card)
        await _watcher_done(card.id)
        back = sc.get_card(card.id)
        assert back.status == sc.STATUS_PENDING
        assert back.error["code"] == signin.CODE_TIMEOUT
        assert "aws_signin" not in (back.outcome or {})
        assert aws["procs"][0].terminated
        assert not signin._live

    @pytest.mark.asyncio
    async def test_cancel_stops_the_child_and_returns_the_card(self, state, aws, monkeypatch):
        card = _home_card(monkeypatch)
        await _sign_in(state, card)
        task = signin._live[card.id].task
        cancelled = await _sign_in(state, card, cancel=True)
        assert cancelled.status == sc.STATUS_PENDING and cancelled.error is None
        assert "aws_signin" not in (cancelled.outcome or {})
        assert aws["procs"][0].terminated
        assert not signin._live
        await asyncio.wait_for(task, 5)
        # The watcher saw the card leave the sign-in and settled nothing over it.
        assert sc.get_card(card.id).error is None

    @pytest.mark.asyncio
    async def test_cancel_refuses_a_card_that_is_not_signing_in(self, state, aws, monkeypatch):
        card = _home_card(monkeypatch)
        with pytest.raises(sc.CardRejected) as exc:
            await _sign_in(state, card, cancel=True)
        assert exc.value.code == "card_not_pending"

    @pytest.mark.asyncio
    async def test_a_child_that_exits_without_signing_in_is_reported(self, state, aws, monkeypatch):
        card = _home_card(monkeypatch)
        await _sign_in(state, card)
        aws["procs"][0].returncode = 1
        await _watcher_done(card.id)
        back = sc.get_card(card.id)
        assert back.status == sc.STATUS_PENDING and back.error["code"] == signin.CODE_FAILED

    @pytest.mark.asyncio
    async def test_a_cli_without_aws_login_says_to_update_it(self, state, aws, monkeypatch):
        card = _home_card(monkeypatch)
        await _sign_in(state, card)
        aws["procs"][0].returncode = signin._CLI_USAGE_ERROR
        await _watcher_done(card.id)
        assert sc.get_card(card.id).error["code"] == signin.CODE_CLI_TOO_OLD

    @pytest.mark.asyncio
    async def test_a_profile_holding_access_keys_is_told_to_use_a_new_profile(
        self, state, aws, monkeypatch
    ):
        card = _home_card(monkeypatch, profile="")
        await _sign_in(state, card)
        # `aws login` refuses at once: "already configured with Access Key credentials".
        aws["procs"][0].returncode = signin._CLI_PROFILE_HAS_KEYS
        await _watcher_done(card.id)
        back = sc.get_card(card.id)
        assert back.status == sc.STATUS_PENDING
        assert back.error["code"] == signin.CODE_PROFILE_HAS_KEYS
        assert "access keys" in back.error["message"]
        assert signin.NEW_PROFILE in back.error["message"]
        # The agent's next home card, under the new profile, signs in that profile.
        again = _home_card(monkeypatch, profile=signin.NEW_PROFILE)
        waiting = await _sign_in(state, again)
        assert waiting.status == sc.STATUS_WAITING
        assert aws["spawned"] == [("", "eu-west-1"), (signin.NEW_PROFILE, "eu-west-1")]

    @pytest.mark.asyncio
    async def test_an_old_cli_is_refused_before_anything_starts(self, state, aws, monkeypatch):
        from kiro_crew.cloud import aws as aws_cli

        monkeypatch.setattr(signin, "_cli_problem", _REAL_CLI_PROBLEM)
        monkeypatch.setattr(signin, "_spawn_login", lambda p, r: pytest.fail("spawned"))
        monkeypatch.setattr(
            aws_cli, "run_aws", lambda args, *a, **kw: (0, "aws-cli/2.31.9 Python/3.13", "")
        )
        card = _home_card(monkeypatch)
        back = await _sign_in(state, card)
        assert back.status == sc.STATUS_PENDING and back.error["code"] == signin.CODE_CLI_TOO_OLD

    @pytest.mark.asyncio
    async def test_one_sign_in_at_a_time(self, state, aws, monkeypatch):
        first = _home_card(monkeypatch)
        second = _home_card(monkeypatch, profile="other")
        await _sign_in(state, first)
        busy = await _sign_in(state, second)
        assert busy.status == sc.STATUS_PENDING and busy.error["code"] == signin.CODE_BUSY
        assert len(aws["spawned"]) == 1

    @pytest.mark.asyncio
    async def test_a_sign_in_whose_watcher_is_gone_can_start_again(self, state, aws, monkeypatch):
        card = _home_card(monkeypatch)
        await _sign_in(state, card)
        # A gateway restart: the process memory is gone, the card still waits.
        live = signin._live.pop(card.id)
        assert live.task is not None
        live.task.cancel()
        with pytest.raises(sc.CardRejected) as exc:
            await _sign_in(state, card)
        assert exc.value.code == "card_not_pending"

        def _expire(c: sc.SetupCard) -> None:
            c.outcome["aws_signin"]["expires_ts"] = time.time() - 3600

        sc.update_card(card.id, _expire)
        again = await _sign_in(state, card)
        assert again.status == sc.STATUS_WAITING and len(aws["spawned"]) == 2

    @pytest.mark.asyncio
    async def test_the_profile_signed_in_is_the_one_the_owner_saw(self, state, aws, monkeypatch):
        card = _home_card(monkeypatch, profile="work")

        # The store is writable from the agent's sandbox; private is not hash-bound.
        def _tamper(c: sc.SetupCard) -> None:
            c.private["settings"]["profile"] = "attacker"

        sc.update_card(card.id, _tamper)
        await _sign_in(state, card)
        assert aws["spawned"] == [("work", "eu-west-1")]

    @pytest.mark.asyncio
    async def test_the_default_profile_passes_no_profile_flag(self, state, aws, monkeypatch):
        card = _home_card(monkeypatch, profile="")
        assert card.payload["profile"] == "default"
        await _sign_in(state, card)
        assert aws["spawned"] == [("", "eu-west-1")]

    @pytest.mark.asyncio
    async def test_only_a_home_card_signs_in(self, state, aws):
        card = sc.create_card(
            slot="chat-1-1",
            session_key="dashboard:chat-1-1",
            kind=sc.KIND_PROFILE,
            payload={"fields": {"bot_name": "Nova"}},
        )
        with pytest.raises(sc.CardRejected) as exc:
            await _sign_in(state, card)
        assert exc.value.code == "invalid_decision"

    @pytest.mark.asyncio
    async def test_a_simulated_home_needs_no_sign_in(self, state, aws, monkeypatch):
        from kiro_crew.cloud import simulated_engine

        monkeypatch.setenv(simulated_engine.SIMULATE_ENV, "1")
        card = _home_card(monkeypatch)
        back = await _sign_in(state, card)
        assert back.error["code"] == signin.CODE_NOT_NEEDED and aws["spawned"] == []

    @pytest.mark.asyncio
    async def test_a_wrong_hash_starts_nothing(self, state, aws, monkeypatch):
        card = _home_card(monkeypatch)
        with pytest.raises(sc.CardRejected) as exc:
            await setup_flow.decide(state, card.id, "aws_signin", "0" * 64, {}, same_machine=True)
        assert exc.value.code == "card_hash_mismatch" and aws["spawned"] == []


class TestSameMachine:
    @pytest.mark.asyncio
    async def test_a_remote_gateway_gets_the_terminal_command_instead(
        self, state, aws, monkeypatch
    ):
        card = _home_card(monkeypatch, profile="work")
        back = await _sign_in(state, card, same_machine=False)
        assert back.status == sc.STATUS_PENDING
        assert back.error["code"] == signin.CODE_REMOTE
        assert "aws login --remote --profile work" in back.error["message"]
        assert back.outcome["aws_signin"] == {
            "state": "remote",
            "command": "aws login --remote --profile work",
        }
        assert aws["spawned"] == []

    def test_the_browser_is_here_only_on_a_local_desktop_with_a_display(self, monkeypatch):
        browser_is_here = _REAL_BROWSER_IS_HERE
        monkeypatch.setenv("KIRO_AUTH_INSTALL_SHAPE", "desktop")
        monkeypatch.delenv("KIROCREW_NO_BROWSER", raising=False)
        monkeypatch.delenv("SSH_CONNECTION", raising=False)
        monkeypatch.delenv("SSH_CLIENT", raising=False)
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setenv("DISPLAY", ":0")
        assert browser_is_here(True) is True
        # The owner's request came through a tunnel or proxy.
        assert browser_is_here(False) is False
        # The gateway runs over SSH or in a container.
        monkeypatch.setenv("KIRO_AUTH_INSTALL_SHAPE", "remote")
        assert browser_is_here(True) is False
        monkeypatch.setenv("KIRO_AUTH_INSTALL_SHAPE", "container")
        assert browser_is_here(True) is False
        # A Linux host with no display cannot open a browser.
        monkeypatch.setenv("KIRO_AUTH_INSTALL_SHAPE", "desktop")
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "BROWSER"):
            monkeypatch.delenv(name, raising=False)
        assert browser_is_here(True) is False

    @pytest.mark.asyncio
    async def test_the_decide_route_says_whether_the_request_is_direct_local(self, monkeypatch):
        from kiro_crew.dashboard.handlers import setup_cards as handlers

        async def _owner(request, operation):
            return None

        seen: list[bool] = []

        async def _decide(state_, card_id, decision, card_hash, input_, *, same_machine=False):
            seen.append(same_machine)
            raise sc.CardRejected("stop", "card_not_pending")

        monkeypatch.setattr(handlers, "require_owner_dashboard_request", _owner)
        monkeypatch.setattr(setup_flow, "decide", _decide)
        app = web.Application()
        app["state"] = FakeState()
        handlers.register_routes(app)
        body = {"decision": "aws_signin", "hash": "0" * 64}
        async with TestClient(TestServer(app)) as client:
            await client.post("/api/setup/cards/sc-0000000000000000/decide", json=body)
            await client.post(
                "/api/setup/cards/sc-0000000000000000/decide",
                json=body,
                headers={"X-Forwarded-For": "203.0.113.9"},
            )
        assert seen == [True, False]


class TestNoCredentials:
    """SC4: Kiro Crew never reads, keeps or logs what the AWS CLI or AWS prints."""

    def test_the_child_hears_nothing_and_says_nothing_to_kiro_crew(self, monkeypatch):
        from kiro_crew.deploy import engine

        monkeypatch.delenv("KIROCREW_SESSION_KEY", raising=False)
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", SENTINEL)
        monkeypatch.setenv("AWS_SESSION_TOKEN", SENTINEL)
        monkeypatch.setattr(engine, "resolve_aws_bin", lambda: "/opt/aws/bin/aws")
        seen: dict[str, Any] = {}

        def _popen(argv, **kw):
            seen["argv"], seen["kw"] = argv, kw
            return FakeProc()

        monkeypatch.setattr(signin.subprocess, "Popen", _popen)
        signin._spawn_login("work", "eu-west-1")
        assert seen["argv"] == [
            "/opt/aws/bin/aws",
            "login",
            "--profile",
            "work",
            "--region",
            "eu-west-1",
        ]
        kw = seen["kw"]
        assert kw["stdin"] is subprocess.DEVNULL
        assert kw["stdout"] is subprocess.DEVNULL
        assert kw["stderr"] is subprocess.DEVNULL
        assert kw["start_new_session"] is True
        assert "shell" not in kw
        assert SENTINEL not in json.dumps(kw["env"])

    def test_the_default_profile_is_signed_in_without_a_profile_flag(self, monkeypatch):
        from kiro_crew.deploy import engine

        monkeypatch.delenv("KIROCREW_SESSION_KEY", raising=False)
        monkeypatch.setattr(engine, "resolve_aws_bin", lambda: "/opt/aws/bin/aws")
        seen: dict[str, Any] = {}

        def _popen(argv, **kw):
            seen["argv"] = argv
            return FakeProc()

        monkeypatch.setattr(signin.subprocess, "Popen", _popen)
        signin._spawn_login("", "eu-west-1")
        assert seen["argv"] == ["/opt/aws/bin/aws", "login", "--region", "eu-west-1"]

    def test_an_agent_session_cannot_start_it(self, monkeypatch):
        from kiro_crew.cloud.aws import CloudActionDenied

        monkeypatch.setenv("KIROCREW_SESSION_KEY", "dashboard:chat-1-1")
        monkeypatch.setattr(signin.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
        with pytest.raises(CloudActionDenied):
            signin._spawn_login("", "us-east-1")

    @pytest.mark.asyncio
    async def test_only_the_last_four_digits_of_the_account_are_kept(
        self, state, monkeypatch, caplog
    ):
        from kiro_crew.cloud import aws as aws_cli

        caplog.set_level(logging.DEBUG)
        answer = {
            "Account": "123456789012",
            "Arn": f"arn:aws:sts::1:{SENTINEL}",
            "UserId": SENTINEL,
        }
        monkeypatch.setattr(aws_cli, "run_aws", lambda args, *a, **kw: (0, json.dumps(answer), ""))
        monkeypatch.setattr(local_signin, "aws_cli_present", lambda: True)
        monkeypatch.setattr(signin, "_spawn_login", lambda p, r: pytest.fail("spawned"))
        card = _home_card(monkeypatch)
        done = await _sign_in(state, card)
        assert done.outcome["aws_account"] == "…9012"
        stored = (data_home() / "setup" / sc.CARDS_FILE).read_text(encoding="utf-8")
        for surface in (stored, json.dumps(state.events), caplog.text, json.dumps(done.public())):
            assert SENTINEL not in surface
            assert "123456789012" not in surface

    def test_a_signed_out_answer_is_parsed_not_echoed(self, monkeypatch):
        from kiro_crew.cloud import aws as aws_cli

        monkeypatch.setattr(aws_cli, "run_aws", lambda args, *a, **kw: (255, "", SENTINEL))
        monkeypatch.setattr(local_signin, "aws_cli_present", lambda: True)
        assert _REAL_SIGNED_IN_ACCOUNT("work") is None
