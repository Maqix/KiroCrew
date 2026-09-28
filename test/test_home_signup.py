"""The home card's path for someone with no AWS account yet (``setup_flow._home_payload``).

A card shown on a machine with no AWS sign-in links the AWS sign-up, the Builder
ID one when this machine's Kiro sign-in is exactly Builder ID, and says whether
the AWS CLI is installed. A signed-in or simulated card carries none of it and
runs no ``kiro-cli whoami``. Nothing here reaches AWS or a real kiro-cli.
"""

from __future__ import annotations

from typing import Any

import pytest

from kiro_crew import setup_cards as sc
from kiro_crew.cloud import iam, local_signin, login_target
from kiro_crew.dashboard import setup_flow

_SIGNUP_KEYS = {"signup_url", "signup_builder_id", "aws_cli_installed"}


@pytest.fixture
def whoami(monkeypatch):
    """What this machine's ``kiro-cli whoami`` answers, and every call to it."""
    world: dict[str, Any] = {"answer": {}, "calls": []}

    def _discover(kiro_bin=None, *, timeout=15.0):
        world["calls"].append(timeout)
        answer = world["answer"]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(login_target, "discover_local_identity", _discover)
    monkeypatch.setattr(local_signin, "aws_cli_present", lambda: True)
    return world


def _payload(monkeypatch, *, reachable: bool) -> dict[str, Any]:
    monkeypatch.setattr(iam, "reachability_check", lambda p, r: {"reachable": reachable})
    payload, _private = setup_flow._home_payload(sc.build_home({"region": "eu-west-1"}))
    return payload


class TestSignup:
    def test_a_builder_id_machine_gets_the_builder_id_signup(self, monkeypatch, whoami):
        whoami["answer"] = {"account_type": login_target.ACCOUNT_TYPE_BUILDER_ID}
        payload = _payload(monkeypatch, reachable=False)
        assert (
            payload["signup_url"] == "https://signin.aws.amazon.com/signup?request_type=builderId"
        )
        assert payload["signup_builder_id"] is True
        assert payload["aws_cli_installed"] is True
        # One bounded whoami.
        assert len(whoami["calls"]) == 1 and whoami["calls"][0] <= 10

    @pytest.mark.parametrize(
        "answer",
        [
            {"account_type": "SocialGoogle"},
            {"account_type": login_target.ACCOUNT_TYPE_IDENTITY_CENTER, "start_url": "x"},
            {},  # no Kiro sign-in here
            None,  # whoami did not answer
            RuntimeError("kiro-cli would not start"),
        ],
    )
    def test_anything_else_gets_the_plain_signup(self, monkeypatch, whoami, answer):
        whoami["answer"] = answer
        payload = _payload(monkeypatch, reachable=False)
        assert payload["signup_url"] == local_signin.SIGNUP_URL
        assert payload["signup_builder_id"] is False

    def test_a_missing_aws_cli_is_on_the_card(self, monkeypatch, whoami):
        monkeypatch.setattr(local_signin, "aws_cli_present", lambda: False)
        assert _payload(monkeypatch, reachable=False)["aws_cli_installed"] is False

    def test_a_signed_in_machine_gets_nothing_and_runs_no_whoami(self, monkeypatch, whoami):
        payload = _payload(monkeypatch, reachable=True)
        assert not _SIGNUP_KEYS & payload.keys()
        assert whoami["calls"] == []

    def test_a_simulated_home_gets_nothing_and_runs_no_whoami(self, monkeypatch, whoami):
        from kiro_crew.cloud import simulated_engine

        monkeypatch.setenv(simulated_engine.SIMULATE_ENV, "1")
        payload = _payload(monkeypatch, reachable=False)
        assert not _SIGNUP_KEYS & payload.keys()
        assert whoami["calls"] == []

    def test_the_signup_pages(self):
        assert local_signin.signup_url(False) == local_signin.SIGNUP_URL
        assert local_signin.signup_url(True).endswith("?request_type=builderId")
        for url in (local_signin.signup_url(False), local_signin.signup_url(True)):
            assert url.startswith("https://signin.aws.amazon.com/signup?")
