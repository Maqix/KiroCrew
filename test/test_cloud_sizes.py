"""Unit tests for the cloud size-tier catalog (cloud/sizes.py)."""

from __future__ import annotations

import pytest

from kiro_crew.cloud import sizes


class TestTierCatalog:
    def test_default_is_recommended_and_32gb(self):
        d = sizes.default_tier()
        assert d.key == sizes.DEFAULT_TIER_KEY
        assert d.recommended is True
        # Default is the 8-vCPU "Development" tier: 16 GB caps sub-agents at the
        # floor (CPU-bound), so the default steps up to 32 GB / 8 vCPU.
        assert d.ram_gb == 32
        assert d.vcpu == 8

    def test_default_is_arm64(self):
        assert sizes.default_tier().arch == sizes.ARCH_ARM64

    def test_no_full_tier_below_the_measured_peak(self):
        # 3 chats + a sub-agent peak at 3.7 GB and the dashboard build at 2.6 GB:
        # 8 GB is the floor for a home that runs everything.
        for t in sizes.all_tiers():
            assert t.vcpu >= 2
            assert t.disk_gb >= 20  # the template's VolumeSizeGb MinValue
            assert t.approx_usd_per_hr > 0
            if t.home_profile == sizes.PROFILE_STANDARD:
                assert t.ram_gb >= 8 and t.disk_gb >= 30, t.key

    def test_every_tier_below_8_gb_runs_a_slimmed_home(self):
        small_ones = {t.key: t.home_profile for t in sizes.all_tiers() if t.ram_gb < 8}
        assert small_ones == {"lite": sizes.PROFILE_LITE, "economy": sizes.PROFILE_ECONOMY}

    def test_lite_and_economy_shapes(self):
        lite, economy = sizes.get_tier("lite"), sizes.get_tier("economy")
        assert (lite.instance_type, lite.arch, lite.vcpu, lite.ram_gb) == (
            "t4g.small",
            sizes.ARCH_ARM64,
            2,
            2,
        )
        assert (economy.instance_type, economy.arch, economy.vcpu, economy.ram_gb) == (
            "t4g.medium",
            sizes.ARCH_ARM64,
            2,
            4,
        )
        # t4g.small is on the Free plan's list of instance types; t4g.medium is not.
        assert lite.free_plan_ok is True and economy.free_plan_ok is False
        assert lite.approx_usd_per_hr < economy.approx_usd_per_hr

    def test_the_template_accepts_every_home_profile(self):
        from kiro_crew.cloud import ec2

        text = ec2.load_template()
        for t in sizes.all_tiers():
            assert t.home_profile in (
                sizes.PROFILE_STANDARD,
                sizes.PROFILE_LITE,
                sizes.PROFILE_ECONOMY,
            )
        assert "AllowedValues: [standard, lite, economy]" in text

    def test_small_is_the_paid_plans_cheapest_full_tier(self):
        small = sizes.get_tier("small")
        assert (small.instance_type, small.arch) == ("t4g.large", sizes.ARCH_ARM64)
        assert (small.vcpu, small.ram_gb) == (2, 8)
        assert small.free_plan_ok is False
        full = [t for t in sizes.all_tiers() if t.home_profile == sizes.PROFILE_STANDARD]
        assert small.approx_usd_per_hr == min(t.approx_usd_per_hr for t in full)

    def test_starter_is_the_free_plans_full_tier(self):
        starter = sizes.get_tier("starter")
        assert (starter.instance_type, starter.arch) == ("m7i-flex.large", sizes.ARCH_X86_64)
        assert (starter.vcpu, starter.ram_gb) == (2, 8)
        assert [t.key for t in sizes.all_tiers() if t.free_plan_ok] == ["lite", "starter"]
        # Not in the CLI wizard's three-choice menu, which stays paid-plan sizes.
        assert "starter" not in sizes.INTERACTIVE_TIER_KEYS
        assert "lite" not in sizes.INTERACTIVE_TIER_KEYS

    def test_ladder_is_cpu_scaled(self):
        # Each arm tier doubles both RAM and vCPU so the sub-agent cap actually
        # rises (the cap is CPU-bound). Power must be 16 vCPU, not a memory shape.
        light, dev, power = (sizes.get_tier(k) for k in ("light", "balanced", "power"))
        assert (light.ram_gb, light.vcpu) == (16, 4)
        assert (dev.ram_gb, dev.vcpu) == (32, 8)
        assert (power.ram_gb, power.vcpu) == (64, 16)
        assert power.instance_type == "m7g.4xlarge"

    def test_get_tier_known(self):
        assert sizes.get_tier("light").instance_type == "t4g.xlarge"
        assert sizes.get_tier("power").instance_type == "m7g.4xlarge"

    def test_get_tier_unknown_lists_valid(self):
        with pytest.raises(KeyError) as ei:
            sizes.get_tier("nope")
        assert "unknown size 'nope'" in str(ei.value)
        assert "balanced" in str(ei.value)

    def test_interactive_tiers_order(self):
        keys = [t.key for t in sizes.interactive_tiers()]
        assert keys == ["light", "balanced", "power"]

    def test_x86_lane_present(self):
        for k in ("light-x86", "balanced-x86", "power-x86"):
            assert sizes.get_tier(k).arch == sizes.ARCH_X86_64

    def test_summary_mentions_specs(self):
        s = sizes.get_tier("balanced").summary()
        assert "m7g.2xlarge" in s
        assert "32 GB" in s
        assert "arm64" in s

    def test_monthly_estimate(self):
        t = sizes.get_tier("balanced")
        # 24h/day * 30 days
        assert sizes.monthly_estimate(t) == round(t.approx_usd_per_hr * 24 * 30, 2)
        # Half-day uptime is roughly half.
        assert sizes.monthly_estimate(t, 12) < sizes.monthly_estimate(t, 24)
