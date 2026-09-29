"""The home card's region picker, and its prices per region.

* ``cloud.local_signin``: one region's read-only probe, and the regions a home
  can be built in.
* ``setup_cards``: the server's check of the owner's pick, and the monthly
  estimate from ``cloud/sizes.py``'s per-region table.
* ``setup_flow``: a card whose account answered in no region asks for one, and
  the ``region`` decision re-issues it for the pick under a new hash.

``aws.run_aws`` is a recorder here, so nothing reaches AWS.
"""

from __future__ import annotations

from typing import Any

import pytest

from kiro_crew import setup_cards as sc
from kiro_crew.cloud import aws, iam, local_signin, sizes
from kiro_crew.dashboard import setup_flow

_DENIED = (254, "", "An error occurred (UnauthorizedOperation) when calling ...")


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


class TestProbeRegion:
    def test_an_answer_a_refusal_and_nothing_known(self, aws_cli):
        aws_cli["answers"][("ec2", "describe-availability-zones", "eu-north-1")] = (0, "{}", "")
        aws_cli["answers"][("ec2", "describe-availability-zones", "us-east-1")] = _DENIED
        assert local_signin.probe_region("p", "eu-north-1") == local_signin.REGION_OK
        assert local_signin.probe_region("p", "us-east-1") == local_signin.REGION_REFUSED
        assert local_signin.probe_region("p", "us-west-2") == local_signin.REGION_UNKNOWN
        assert [c[2] for c in aws_cli["calls"]] == ["eu-north-1", "us-east-1", "us-west-2"]

    def test_a_malformed_region_is_never_sent(self, aws_cli):
        assert local_signin.probe_region("p", "us-east-1; rm -rf") == local_signin.REGION_UNKNOWN
        assert aws_cli["calls"] == []

    def test_the_home_regions_cover_every_region_the_card_names(self):
        regions = local_signin.HOME_REGIONS
        assert len(set(regions)) == len(regions)
        assert all(sc._REGION_RE.match(r) for r in regions)
        for region in (sc.HOME_DEFAULT_REGION, *local_signin.HOME_REGION_CANDIDATES):
            assert region in regions
        assert set(sizes.ON_DEMAND_USD_PER_HR) <= set(regions)


class TestValidateRegion:
    def test_a_listed_region_passes(self):
        assert sc.validate_home_region(" eu-north-1 ") == "eu-north-1"

    @pytest.mark.parametrize(
        "value", ["me-central-1", "us-east-1; rm -rf /", "US-EAST-1", "", None, 3, ["us-east-1"]]
    )
    def test_anything_else_is_refused(self, value):
        with pytest.raises(sc.CardRejected) as exc:
            sc.validate_home_region(value)
        assert exc.value.code == "home_region_not_offered"


class TestPricesPerRegion:
    def test_the_table_prices_every_size_the_card_offers_in_every_region(self):
        offered = {key for keys, _default in sc.HOME_PLAN_SIZES.values() for key in keys}
        types = {sizes.get_tier(key).instance_type for key in offered}
        assert set(sizes.GP3_USD_PER_GB_MONTH) == set(sizes.ON_DEMAND_USD_PER_HR)
        for region, prices in sizes.ON_DEMAND_USD_PER_HR.items():
            assert types <= set(prices), region
            assert all(price > 0 for price in prices.values())

    def test_us_east_1_agrees_with_the_tiers_own_figure(self):
        # The CLI prints approx_usd_per_hr; the card's us-east-1 figure is the same.
        for key in ("lite", "economy", "small", "light", "starter"):
            tier = sizes.get_tier(key)
            assert sizes.ON_DEMAND_USD_PER_HR["us-east-1"][tier.instance_type] == pytest.approx(
                tier.approx_usd_per_hr
            )

    def test_each_region_has_its_own_price(self):
        assert [
            sc.monthly_estimate_usd("small", r)
            for r in ("us-east-1", "us-east-2", "eu-north-1", "ap-southeast-2")
        ] == [51, 51, 53, 65]
        assert sc.monthly_estimate_usd("lite", "ap-southeast-2") == 17
        assert sc.monthly_estimate_usd("starter", "ap-southeast-2") == 90

    def test_a_region_not_in_the_table_gets_the_us_east_1_figure(self):
        assert sc.monthly_estimate_usd("small", "eu-west-3") == sc.monthly_estimate_usd(
            "small", "us-east-1"
        )
        assert sizes.region_prices(sizes.get_tier("small"), "eu-west-3")[2] == "us-east-1"
        assert sc.monthly_estimate_usd("small") == 51

    def test_a_type_in_no_table_falls_back_to_the_tier(self):
        tier = sizes.get_tier("power-x86")
        assert sizes.region_prices(tier, "eu-north-1") == (tier.approx_usd_per_hr, 0.08, "")

    def test_the_card_prices_its_options_in_its_region(self):
        options, _ = sc.home_size_options({"type": "PAID"}, "ap-southeast-2")
        assert {o["key"]: o["monthly_usd"] for o in options} == {
            "lite": 17,
            "economy": 33,
            "small": 65,
            "light": 128,
        }


class _State:
    def __init__(self) -> None:
        self.events: list = []

    def get_slot(self, key):
        return None

    def broadcast_ws_owners(self, msg_type, data):
        self.events.append((msg_type, data))


@pytest.fixture
def account(monkeypatch):
    """A signed-in account on the paid plan; which regions answer is the test's."""
    world: dict[str, Any] = {"answers": {}, "probes": [], "walks": []}

    def _resolve(profile, preferred=""):
        world["walks"].append(preferred)
        return ""

    def _probe(profile, region):
        world["probes"].append(region)
        return world["answers"].get(region, local_signin.REGION_REFUSED)

    monkeypatch.setattr(iam, "reachability_check", lambda p, r: {"reachable": True})
    monkeypatch.setattr(local_signin, "resolve_home_region", _resolve)
    monkeypatch.setattr(local_signin, "probe_region", _probe)
    monkeypatch.setattr(local_signin, "account_plan", lambda profile: {"type": "PAID"})
    monkeypatch.setattr(local_signin, "configured_region", lambda profile="": "eu-west-1")
    monkeypatch.setattr(local_signin, "kiro_signs_in_with_builder_id", lambda: False)
    monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: None)
    return world


def _unknown_card() -> sc.SetupCard:
    payload, private = setup_flow._home_payload(sc.build_home({"region": "us-east-1"}))
    return sc.create_card(
        slot="chat-1-1",
        session_key="dashboard:chat-1-1",
        kind=sc.KIND_HOME,
        payload={**payload, setup_flow.HOME_STEP_KEY: True},
        private=private,
    )


class TestNoRegionAnswers:
    def test_the_card_asks_starting_from_the_profiles_region(self, account):
        payload, private = setup_flow._home_payload(sc.build_home({"region": "us-east-1"}))
        assert payload["region_unknown"] is True
        assert payload["region_choices"] == list(local_signin.HOME_REGIONS)
        assert payload["region"] == "eu-west-1" == private["settings"]["region"]
        # eu-west-1 is not in the price table: the us-east-1 figure.
        assert payload["monthly_usd"] == sc.monthly_estimate_usd("light", "us-east-1")

    def test_a_profile_region_the_card_cannot_offer_keeps_the_cards(self, account, monkeypatch):
        monkeypatch.setattr(local_signin, "configured_region", lambda profile="": "me-central-1")
        payload, _ = setup_flow._home_payload(sc.build_home({"region": "us-east-2"}))
        assert payload["region_unknown"] is True and payload["region"] == "us-east-2"

    def test_a_region_that_answers_asks_nothing(self, account, monkeypatch):
        monkeypatch.setattr(local_signin, "resolve_home_region", lambda p, r="": "ap-southeast-2")
        payload, _ = setup_flow._home_payload(sc.build_home({"region": "us-east-1"}))
        assert "region_unknown" not in payload and "region_choices" not in payload
        assert payload["region"] == "ap-southeast-2"
        assert payload["monthly_usd"] == sc.monthly_estimate_usd("light", "ap-southeast-2")


class TestTheRegionDecision:
    @pytest.mark.asyncio
    async def test_a_pick_that_answers_reissues_the_card_for_it(self, account):
        card = _unknown_card()
        account["answers"]["eu-north-1"] = local_signin.REGION_OK
        state = _State()
        out = await setup_flow.decide(
            state, card.id, "region", card.payload_hash, {"region": "eu-north-1"}
        )
        assert account["probes"] == ["eu-north-1"]
        # Only the pick was probed: no second walk over the other regions.
        assert account["walks"] == ["us-east-1"]
        assert out.status == sc.STATUS_PENDING and out.error is None
        assert out.payload_hash != card.payload_hash
        assert out.payload["region"] == "eu-north-1"
        assert "region_unknown" not in out.payload
        assert out.payload[setup_flow.HOME_STEP_KEY] is True
        assert out.payload["size_options"][0]["monthly_usd"] == sc.monthly_estimate_usd(
            "lite", "eu-north-1"
        )
        assert sc.get_card(card.id).private["settings"]["region"] == "eu-north-1"
        assert state.events[-1][1]["card"]["hash"] == out.payload_hash
        # A Build carrying the old face's hash commits nothing.
        with pytest.raises(sc.CardRejected) as exc:
            await setup_flow.decide(state, card.id, "commit", card.payload_hash, {})
        assert exc.value.code == "card_hash_mismatch"

    @pytest.mark.asyncio
    async def test_a_pick_that_does_not_answer_is_kept_and_says_so(self, account):
        card = _unknown_card()
        out = await setup_flow.decide(
            _State(), card.id, "region", card.payload_hash, {"region": "us-west-2"}
        )
        assert out.status == sc.STATUS_PENDING
        assert out.error is not None and out.error["code"] == "home_region_no_answer"
        assert out.payload["region"] == "us-west-2" and out.payload["region_unknown"] is True
        assert sc.get_card(card.id).private["settings"]["region"] == "us-west-2"

    @pytest.mark.asyncio
    async def test_a_region_the_card_does_not_offer_is_refused_unprobed(self, account):
        card = _unknown_card()
        with pytest.raises(sc.CardRejected) as exc:
            await setup_flow.decide(
                _State(), card.id, "region", card.payload_hash, {"region": "me-central-1"}
            )
        assert exc.value.code == "home_region_not_offered"
        assert account["probes"] == []
        assert sc.get_card(card.id).payload_hash == card.payload_hash

    @pytest.mark.asyncio
    async def test_a_stale_hash_is_refused(self, account):
        card = _unknown_card()
        with pytest.raises(sc.CardRejected) as exc:
            await setup_flow.decide(_State(), card.id, "region", "0" * 64, {"region": "eu-north-1"})
        assert exc.value.code == "card_hash_mismatch" and account["probes"] == []

    @pytest.mark.asyncio
    async def test_a_card_that_does_not_ask_takes_no_region(self, account, monkeypatch):
        monkeypatch.setattr(local_signin, "resolve_home_region", lambda p, r="": "us-east-1")
        payload, private = setup_flow._home_payload(sc.build_home({"region": "us-east-1"}))
        card = sc.create_card(
            slot="chat-1-1",
            session_key="dashboard:chat-1-1",
            kind=sc.KIND_HOME,
            payload=payload,
            private=private,
        )
        with pytest.raises(sc.CardRejected) as exc:
            await setup_flow.decide(
                _State(), card.id, "region", card.payload_hash, {"region": "eu-north-1"}
            )
        assert exc.value.code == "invalid_decision" and account["probes"] == []

    @pytest.mark.asyncio
    async def test_governance_can_refuse_it(self, account, monkeypatch):
        card = _unknown_card()
        monkeypatch.setattr(setup_flow, "_governance_denial", lambda kind, sk: "no setup cards")
        with pytest.raises(sc.CardRejected) as exc:
            await setup_flow.decide(
                _State(), card.id, "region", card.payload_hash, {"region": "eu-north-1"}
            )
        assert exc.value.code == "governance_denied" and account["probes"] == []
