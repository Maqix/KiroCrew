"""The home card's sizes, and the AWS account they are offered for.

* ``cloud.local_signin``: the account's AWS plan (``freetier get-account-plan-state``),
  the region it can build in (``ec2 describe-availability-zones``, the profile's
  region first, then a new account's three home regions), and its EC2 vCPU quota.
  All read-only; ``aws.run_aws`` is a recorder here, so nothing reaches AWS.
* ``setup_flow``: the payload's ``size_options`` and default per plan, the Build
  click's checks (an offered size, the Free plan's Starter, the vCPU quota), a
  spend-limit refusal's code, and the payload recomputed under a new hash once
  the owner signs in to AWS from the card.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from kiro_crew import setup_cards as sc
from kiro_crew.cloud import aws, iam
from kiro_crew.cloud import launch_job as lj
from kiro_crew.cloud import local_signin
from kiro_crew.dashboard import handlers_cloud, setup_aws_signin, setup_flow

FREE = {
    "accountPlanType": "FREE",
    "accountPlanStatus": "ACTIVE",
    "accountPlanRemainingCredits": {"amount": 187.5, "unit": "USD"},
    "accountPlanExpirationDate": "2027-03-28T00:00:00Z",
}


@pytest.fixture
def aws_cli(monkeypatch):
    """Scripted ``aws`` answers by (service, operation, region), and every call."""
    world: dict[str, Any] = {"answers": {}, "calls": []}

    def _run(args, profile="", region="", **kw):
        key = (args[0], args[1], region)
        world["calls"].append(key)
        answer = world["answers"].get(key, world["answers"].get((args[0], args[1], "*")))
        return answer if answer is not None else (255, "", "unscripted")

    monkeypatch.setattr(aws, "run_aws", _run)
    monkeypatch.setattr(local_signin, "configured_region", lambda profile="": "")
    return world


class TestAccountPlan:
    def test_a_free_plan_with_its_credits_and_end(self, aws_cli):
        aws_cli["answers"][("freetier", "get-account-plan-state", "us-east-1")] = (
            0,
            json.dumps(FREE),
            "",
        )
        assert local_signin.account_plan("p") == {
            "type": "FREE",
            "credits_usd": 187.5,
            "expires": "2027-03-28T00:00:00Z",
        }

    def test_a_paid_plan(self, aws_cli):
        aws_cli["answers"][("freetier", "get-account-plan-state", "*")] = (
            0,
            json.dumps({"accountPlanType": "PAID", "accountPlanStatus": "ACTIVE"}),
            "",
        )
        assert local_signin.account_plan("p") == {"type": "PAID"}

    def test_an_account_older_than_the_plans_is_paid(self, aws_cli):
        aws_cli["answers"][("freetier", "get-account-plan-state", "*")] = (
            254,
            "",
            "An error occurred (ResourceNotFoundException): Missing data for account",
        )
        assert local_signin.account_plan("p") == {"type": "PAID"}

    def test_any_other_answer_is_unknown(self, aws_cli):
        aws_cli["answers"][("freetier", "get-account-plan-state", "*")] = (255, "", "timeout")
        assert local_signin.account_plan("p", "eu-north-1") == {"type": "unknown"}
        # Retried where the plan API answers.
        assert aws_cli["calls"] == [
            ("freetier", "get-account-plan-state", "eu-north-1"),
            ("freetier", "get-account-plan-state", "us-east-1"),
        ]
        aws_cli["answers"][("freetier", "get-account-plan-state", "*")] = (0, "not json", "")
        assert local_signin.account_plan("p") == {"type": "unknown"}
        aws_cli["answers"][("freetier", "get-account-plan-state", "*")] = (0, "{}", "")
        assert local_signin.account_plan("p") == {"type": "unknown"}

    def test_the_plan_is_never_changed(self):
        import inspect

        assert "upgrade-account-plan" not in inspect.getsource(local_signin)


class TestHomeRegion:
    def test_the_profiles_region_first_then_a_new_accounts_three(self, aws_cli, monkeypatch):
        monkeypatch.setattr(local_signin, "configured_region", lambda profile="": "eu-west-1")
        denied = (254, "", "An error occurred (UnauthorizedOperation) when calling ...")
        for region in ("us-west-2", "eu-west-1", "us-east-2"):
            aws_cli["answers"][("ec2", "describe-availability-zones", region)] = denied
        aws_cli["answers"][("ec2", "describe-availability-zones", "eu-north-1")] = (0, "{}", "")
        assert local_signin.resolve_home_region("p", "us-west-2") == "eu-north-1"
        assert [c[2] for c in aws_cli["calls"]] == [
            "us-west-2",
            "eu-west-1",
            "us-east-2",
            "eu-north-1",
        ]

    def test_the_preferred_region_when_it_answers(self, aws_cli):
        aws_cli["answers"][("ec2", "describe-availability-zones", "us-east-1")] = (0, "{}", "")
        assert local_signin.resolve_home_region("p", "us-east-1") == "us-east-1"
        assert len(aws_cli["calls"]) == 1

    def test_a_failure_that_is_not_a_refusal_knows_nothing(self, aws_cli):
        aws_cli["answers"][("ec2", "describe-availability-zones", "us-east-1")] = (
            255,
            "",
            "Could not connect to the endpoint URL",
        )
        assert local_signin.resolve_home_region("p", "us-east-1") == ""
        assert len(aws_cli["calls"]) == 1

    def test_no_region_answers(self, aws_cli):
        denied = (254, "", "AccessDenied")
        aws_cli["answers"][("ec2", "describe-availability-zones", "*")] = denied
        assert local_signin.resolve_home_region("p", "") == ""
        assert [c[2] for c in aws_cli["calls"]] == list(local_signin.HOME_REGION_CANDIDATES)


class TestVcpuQuota:
    def test_the_quota(self, aws_cli):
        aws_cli["answers"][("service-quotas", "get-service-quota", "eu-north-1")] = (
            0,
            json.dumps({"Quota": {"QuotaCode": "L-1216C47A", "Value": 16.0}}),
            "",
        )
        assert local_signin.vcpu_quota("p", "eu-north-1") == 16

    def test_unknown(self, aws_cli):
        assert local_signin.vcpu_quota("p", "eu-north-1") is None


def _payload(monkeypatch, *, reachable: bool, facts=("", {"type": "unknown"})):
    monkeypatch.setattr(iam, "reachability_check", lambda p, r: {"reachable": reachable})
    monkeypatch.setattr(setup_flow, "_account_facts", lambda profile, region: facts)
    monkeypatch.setattr(local_signin, "kiro_signs_in_with_builder_id", lambda: False)
    return setup_flow._home_payload(sc.build_home({"region": "us-east-1"}))


class TestSizeOptions:
    def test_a_free_plan_defaults_to_starter_with_its_credit(self, monkeypatch):
        facts = ("eu-north-1", {"type": "FREE", "credits_usd": 187.5})
        payload, private = _payload(monkeypatch, reachable=True, facts=facts)
        # Cheapest first: Lite, Starter (both Free-plan types), then Standard.
        assert [o["key"] for o in payload["size_options"]] == ["lite", "starter", "light"]
        _lite, starter, standard = payload["size_options"]
        assert (starter["instance_type"], starter["vcpu"], starter["ram_gb"]) == (
            "m7i-flex.large",
            2,
            8,
        )
        assert starter["free_plan_ok"] is True and standard["free_plan_ok"] is False
        assert starter["label"] == "Starter" and standard["label"] == "Standard"
        assert (starter["note"], standard["note"]) == ("free_plan_credits", "many_chats")
        # Priced in the account's own region.
        assert starter["monthly_usd"] == sc.monthly_estimate_usd("starter", "eu-north-1") == 77
        weeks = int(187.5 / (starter["monthly_usd"] / (52 / 12)))
        assert starter["credit_weeks"] == weeks and starter["credits_usd"] == 187.5
        assert payload["size_default"] == "starter"
        assert payload["plan"] == {"type": "FREE", "credits_usd": 187.5}
        # The account's own region, on the card and in what Build uses.
        assert payload["region"] == "eu-north-1"
        assert private["settings"]["region"] == "eu-north-1"

    def test_a_paid_plan_preselects_small(self, monkeypatch):
        payload, _ = _payload(monkeypatch, reachable=True, facts=("", {"type": "PAID"}))
        options = payload["size_options"]
        # Cheapest first: Lite, Economy, Small, then Standard. Starter costs more
        # than Small, so a paid account is not offered it; Lite costs less, so it is.
        assert [o["key"] for o in options] == ["lite", "economy", "small", "light"]
        small = options[2]
        assert (small["label"], small["instance_type"], small["vcpu"], small["ram_gb"]) == (
            "Small",
            "t4g.large",
            2,
            8,
        )
        assert small["note"] == "few_chats" and small["free_plan_ok"] is False
        assert small["monthly_usd"] < options[3]["monthly_usd"]
        assert [o["monthly_usd"] for o in options] == sorted(o["monthly_usd"] for o in options)
        assert payload["size_default"] == "small"
        assert payload["region"] == "us-east-1"

    def test_a_free_plan_size_no_dearer_than_small_joins_the_paid_list(self, monkeypatch):
        from kiro_crew.cloud import sizes

        # Starter cheaper than Small in one region only: the rule is decided there.
        monkeypatch.setitem(sizes.ON_DEMAND_USD_PER_HR["us-east-2"], "m7i-flex.large", 0.05)
        options, default = sc.home_size_options({"type": "PAID"}, "us-east-2")
        assert [o["key"] for o in options] == ["lite", "economy", "starter", "small", "light"]
        assert default == "small"
        options, _ = sc.home_size_options({"type": "PAID"}, "us-east-1")
        assert "starter" not in [o["key"] for o in options]

    def test_lite_says_what_it_gives_up_and_what_the_credit_buys(self):
        options, _ = sc.home_size_options({"type": "FREE", "credits_usd": 100})
        lite = options[0]
        assert (lite["key"], lite["instance_type"], lite["ram_gb"]) == ("lite", "t4g.small", 2)
        assert lite["note"] == "lite_tradeoffs" and lite["free_plan_ok"] is True
        # About $14 a month with its disk, so $100 of credit lasts about 30 weeks.
        assert lite["monthly_usd"] == sc.monthly_estimate_usd("lite") == 14
        assert lite["credit_weeks"] == 30

    def test_economy_is_paid_plan_only(self):
        free, _ = sc.home_size_options({"type": "FREE"})
        paid, _ = sc.home_size_options({"type": "PAID"})
        assert "economy" not in [o["key"] for o in free]
        economy = next(o for o in paid if o["key"] == "economy")
        assert (economy["instance_type"], economy["ram_gb"]) == ("t4g.medium", 4)
        assert economy["note"] == "all_on" and economy["free_plan_ok"] is False
        assert economy["monthly_usd"] == 26

    def test_the_offers_are_data(self):
        # Every offered size is a tier with a label and a note; each plan's default
        # is one of its own sizes.
        from kiro_crew.cloud import sizes

        for keys, default in sc.HOME_PLAN_SIZES.values():
            assert default in keys
            for key in keys:
                assert key in sizes.TIERS_BY_KEY and key in sc.HOME_SIZE_OFFERS

    def test_signed_out_shows_both_and_asks_aws_nothing(self, monkeypatch):
        def _no_facts(profile, region):
            raise AssertionError("no plan or region probe without a sign-in")

        monkeypatch.setattr(iam, "reachability_check", lambda p, r: {"reachable": False})
        monkeypatch.setattr(setup_flow, "_account_facts", _no_facts)
        monkeypatch.setattr(local_signin, "kiro_signs_in_with_builder_id", lambda: False)
        payload, _ = setup_flow._home_payload(sc.build_home({"region": "us-east-1"}))
        # A plan not known yet gets the Free plan's list: a new account starts on it.
        assert [o["key"] for o in payload["size_options"]] == ["lite", "starter", "light"]
        assert payload["size_default"] == "starter" and "plan" not in payload


class _State:
    def __init__(self) -> None:
        self.events: list = []

    def get_slot(self, key):
        return None

    def broadcast_ws_owners(self, msg_type, data):
        self.events.append((msg_type, data))


@pytest.fixture
def build(monkeypatch):
    """A signed-in home card and the launch it would start."""
    started: list[dict[str, Any]] = []

    async def _start(state_, **kw):
        started.append(kw)
        job = lj.LaunchJob(
            id="job-1", profile="", region=kw["region"], size_key=kw["size_key"], status="running"
        )
        return job, None

    async def _no_target():
        return None

    async def _no_watch(*a, **kw):
        return None

    monkeypatch.setattr(handlers_cloud, "start_launch_job", _start)
    monkeypatch.setattr(setup_flow, "_inherited_login_target", _no_target)
    monkeypatch.setattr(setup_flow, "_watch_home", _no_watch)
    monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: None)
    monkeypatch.setattr(local_signin, "vcpu_quota", lambda profile, region: 32)
    return started


def _card(monkeypatch, plan: dict[str, Any]) -> sc.SetupCard:
    payload, private = _payload(monkeypatch, reachable=True, facts=("eu-north-1", plan))
    return sc.create_card(
        slot="chat-1-1",
        session_key="dashboard:chat-1-1",
        kind=sc.KIND_HOME,
        payload=payload,
        private=private,
    )


class TestTheBuildClick:
    @pytest.mark.asyncio
    async def test_the_chosen_size_is_built_in_the_cards_region(self, monkeypatch, build):
        card = _card(monkeypatch, {"type": "PAID"})
        out = await setup_flow.decide(
            _State(), card.id, "commit", card.payload_hash, {"size": "light"}
        )
        assert out.status == sc.STATUS_WAITING
        assert build[0]["size_key"] == "light" and build[0]["region"] == "eu-north-1"
        assert sc.get_card(card.id).private["settings"]["size"] == "light"

    @pytest.mark.asyncio
    async def test_no_size_given_builds_the_default(self, monkeypatch, build):
        card = _card(monkeypatch, {"type": "PAID"})
        await setup_flow.decide(_State(), card.id, "commit", card.payload_hash, {})
        assert build[0]["size_key"] == "small"

    @pytest.mark.asyncio
    async def test_a_size_the_card_did_not_offer_is_refused(self, monkeypatch, build):
        card = _card(monkeypatch, {"type": "PAID"})
        out = await setup_flow.decide(
            _State(), card.id, "commit", card.payload_hash, {"size": "power"}
        )
        assert out.error["code"] == "home_size_not_offered" and build == []

    @pytest.mark.asyncio
    async def test_the_free_plan_builds_starter_only(self, monkeypatch, build):
        card = _card(monkeypatch, {"type": "FREE", "credits_usd": 100})
        out = await setup_flow.decide(
            _State(), card.id, "commit", card.payload_hash, {"size": "light"}
        )
        assert out.status == sc.STATUS_PENDING
        assert out.error["code"] == "home_size_needs_paid_plan" and build == []

    @pytest.mark.asyncio
    async def test_a_vcpu_quota_below_the_size_is_refused(self, monkeypatch, build):
        monkeypatch.setattr(local_signin, "vcpu_quota", lambda profile, region: 2)
        card = _card(monkeypatch, {"type": "PAID"})
        out = await setup_flow.decide(
            _State(), card.id, "commit", card.payload_hash, {"size": "light"}
        )
        assert out.error["code"] == "home_vcpu_quota_low" and build == []
        # Small's 2 vCPUs fit that quota.
        ok = sc.get_card(card.id)
        await setup_flow.decide(_State(), ok.id, "commit", ok.payload_hash, {"size": "small"})
        assert build[0]["size_key"] == "small"


class TestSpendLimit:
    @pytest.mark.asyncio
    async def test_a_launch_stopped_by_the_spend_limit_says_so(self, monkeypatch):
        card = _card(monkeypatch, {"type": "PAID"})

        def _waiting(c):
            c.status = sc.STATUS_WAITING
            c.private["job_id"] = "job-1"

        sc.update_card(card.id, _waiting)
        failed = lj.LaunchJob(
            id="job-1",
            profile="",
            region="eu-north-1",
            size_key="light",
            status=lj.FAILED,
            error="CREATE_FAILED: the account has reached its spend limit for this month",
        )

        class _Store:
            def get(self, job_id):
                return failed

        monkeypatch.setattr(handlers_cloud, "_store", lambda st: _Store())
        monkeypatch.setattr(setup_flow, "_CONNECT_POLL_SECS", 0)

        async def _no_report(state_, card_):
            return None

        monkeypatch.setattr(setup_flow, "_report", _no_report)
        await asyncio.wait_for(setup_flow._watch_home(_State(), card.id, "job-1"), 5)
        out = sc.get_card(card.id)
        assert out.status == sc.STATUS_FAILED and out.error["code"] == "home_spend_limit"


class _Proc:
    pid = 4242

    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode


class TestAfterTheSignIn:
    def test_the_card_waits_as_long_as_the_aws_cli(self):
        assert setup_aws_signin.SIGNIN_WAIT_SECS == 600

    @pytest.mark.asyncio
    async def test_the_payload_is_recomputed_under_a_new_hash(self, monkeypatch, build):
        # Shown signed out: both sizes, no plan.
        payload, private = _payload(monkeypatch, reachable=False)
        card = sc.create_card(
            slot="chat-1-1",
            session_key="dashboard:chat-1-1",
            kind=sc.KIND_HOME,
            payload={**payload, setup_flow.HOME_STEP_KEY: True},
            private=private,
        )
        proc = _Proc()
        answer: dict[str, Any] = {"account": None}
        monkeypatch.setattr(setup_aws_signin, "_POLL_SECS", 0)
        monkeypatch.setattr(setup_aws_signin, "_EXIT_GRACE_SECS", 0)
        monkeypatch.setattr(setup_aws_signin, "_cli_problem", lambda: None)
        monkeypatch.setattr(setup_aws_signin, "browser_is_here", lambda same_machine: True)
        monkeypatch.setattr(setup_aws_signin, "_signed_in_account", lambda p: answer["account"])
        monkeypatch.setattr(setup_aws_signin, "_spawn_login", lambda p, r: proc)
        state = _State()
        await setup_flow.decide(
            state, card.id, "aws_signin", card.payload_hash, {}, same_machine=True
        )
        # AWS answers now, for a new account on the Free plan in its own region.
        answer["account"] = "…9012"
        proc.returncode = 0
        monkeypatch.setattr(iam, "reachability_check", lambda p, r: {"reachable": True})
        monkeypatch.setattr(
            setup_flow,
            "_account_facts",
            lambda profile, region: ("eu-north-1", {"type": "FREE", "credits_usd": 150}),
        )
        task = setup_aws_signin._live[card.id].task
        await asyncio.wait_for(task, 5)
        fresh = sc.get_card(card.id)
        assert fresh.status == sc.STATUS_PENDING
        assert fresh.payload_hash != card.payload_hash
        assert fresh.payload["aws_signed_in"] is True
        assert fresh.payload["region"] == "eu-north-1"
        assert fresh.payload["plan"]["type"] == "FREE"
        assert fresh.payload["size_default"] == "starter"
        assert fresh.payload[setup_flow.HOME_STEP_KEY] is True
        assert fresh.private["settings"]["region"] == "eu-north-1"
        # The owner saw the new card: its broadcast carries the new hash.
        assert state.events[-1][1]["card"]["hash"] == fresh.payload_hash
        # A Build carrying the hash of the face they saw before commits nothing.
        with pytest.raises(sc.CardRejected) as exc:
            await setup_flow.decide(state, card.id, "commit", card.payload_hash, {})
        assert exc.value.code == "card_hash_mismatch" and build == []
