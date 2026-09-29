"""Instance size tiers for the cloud launcher (constants — no magic numbers).

Measured with a real kiro-cli: an idle gateway is about 1.3 GB, each
open chat adds about 0.4-0.5 GB and stays alive after its last turn, three chats
plus a sub-agent peak at 3.7 GB, and the on-box dashboard build peaks at 2.6 GB.
So 8 GB carries the main chat, a few other chats and scheduled jobs.

Below 8 GB a tier leans on two things. The launcher ships the dashboard it
built (``cloud/source.py``), so the home runs no build; and the tier's
``home_profile`` slims what the home runs, set by the template in the home's
own config and unit:

* **Lite** (``t4g.small``, 2 GB, ``PROFILE_LITE``) turns off embeddings (idle
  falls to about 0.55 GB; memory search is keyword-only) and local
  speech-to-text, starts the background session on first use, spawns no chat
  before its first message, and ends a chat idle for 15 minutes. About three
  things at once. Free-plan eligible.
* **Economy** (``t4g.medium``, 4 GB, ``PROFILE_ECONOMY``) keeps everything on and
  ends a chat idle for 30 minutes. About six things at once. Paid plan.

**Small** (``t4g.large``, arm64, 2 vCPU, 8 GB) is the cheapest full tier, on
the paid plan. **Starter** is the full tier a new AWS account's Free plan allows:
its EC2 runs free-tier types only, and ``m7i-flex.large`` (x86_64, 2 vCPU, 8 GB)
is the smallest of those that fits (``free_plan_ok``). Which tiers a home card
offers on which plan is data in ``setup_cards.HOME_PLAN_SIZES``.

The tiers are laddered by **how much runs at once**, not by GB: the sub-agent
concurrency cap is CPU-bound (``subagent.py`` derives it from vCPU), so RAM alone
does not raise it. Effective parallel sub-agents per tier:

* **Light** — 16 GB / 4 vCPU  → ~3 sub-agents (the floor); single-threaded work.
* **Development** (default, recommended) — 32 GB / 8 vCPU → ~6 sub-agents; the
  normal working setup.
* **Power** — 64 GB / **16 vCPU** → ~12 sub-agents; large fan-outs, long builds.
  (The vCPU count is what matters — a 64 GB box with only 8 vCPU would still cap
  at ~6, so the Power tier is ``m7g.4xlarge`` / 16 vCPU, not a memory-optimized
  shape.)

We default to **arm64 / Graviton** (cheaper per GB; both Kiro Crew and ``kiro-cli``
ship aarch64 Linux builds), with an x86_64 lane for users who need it.

Prices are illustrative on-demand USD/hour and are surfaced only as "approximate"
in the CLI — never used for anything but display, and the dashboard shows no
dollar figure at all (it links the AWS Pricing Calculator instead).
"""

from __future__ import annotations

from dataclasses import dataclass

# CloudFormation architecture parameter values (also select the AMI alias).
ARCH_ARM64 = "arm64"
ARCH_X86_64 = "x86_64"

# The template's HomeProfile values: what a home of the tier runs.
PROFILE_STANDARD = "standard"
PROFILE_LITE = "lite"
PROFILE_ECONOMY = "economy"


@dataclass(frozen=True)
class SizeTier:
    """One selectable instance size."""

    key: str  # stable id used on the CLI (--size) and in config
    label: str  # short human label
    instance_type: str  # EC2 instance type
    arch: str  # ARCH_ARM64 | ARCH_X86_64
    vcpu: int
    ram_gb: int
    disk_gb: int  # gp3 root volume size
    approx_usd_per_hr: float  # illustrative on-demand price
    recommended: bool = False
    #: Launchable on a new AWS account's Free plan, whose EC2 allows free-tier types only.
    free_plan_ok: bool = False
    #: The template's HomeProfile: PROFILE_STANDARD, or a slimmed PROFILE_LITE /
    #: PROFILE_ECONOMY for a tier below 8 GB.
    home_profile: str = PROFILE_STANDARD

    def summary(self) -> str:
        """One-line human summary for menus and confirmations."""
        arch = "arm64" if self.arch == ARCH_ARM64 else "x86_64"
        return (
            f"{self.instance_type}  {arch} · {self.ram_gb} GB · {self.vcpu} vCPU · "
            f"{self.disk_gb} GB disk  ~${self.approx_usd_per_hr:.2f}/hr"
        )


# arm64 / Graviton tiers (the default lane). Light is burstable t4g (cheap at
# idle, bursts for tool calls); Development/Power use non-burstable m7g for
# sustained parallel load. Keys (light/balanced/power) are stable CLI --size ids
# — the ladder was raised (8 GB retired) but the keys did not change, so existing
# --size values and saved configs keep resolving.
_TIERS: tuple[SizeTier, ...] = (
    # The two slimmed tiers, on burstable Graviton. Lite's type is on the Free
    # plan's list; Economy's is not.
    SizeTier(
        key="lite",
        label="Lite",
        instance_type="t4g.small",
        arch=ARCH_ARM64,
        vcpu=2,
        ram_gb=2,
        disk_gb=20,
        approx_usd_per_hr=0.0168,
        free_plan_ok=True,
        home_profile=PROFILE_LITE,
    ),
    SizeTier(
        key="economy",
        label="Economy",
        instance_type="t4g.medium",
        arch=ARCH_ARM64,
        vcpu=2,
        ram_gb=4,
        disk_gb=20,
        approx_usd_per_hr=0.0336,
        home_profile=PROFILE_ECONOMY,
    ),
    # The Free plan's tier: x86_64 (the template's Architecture=x86_64 AMI path),
    # priced at us-east-1 / us-east-2 on-demand; other regions run up to ~$87/mo.
    SizeTier(
        key="starter",
        label="Starter",
        instance_type="m7i-flex.large",
        arch=ARCH_X86_64,
        vcpu=2,
        ram_gb=8,
        disk_gb=30,
        approx_usd_per_hr=0.09576,
        free_plan_ok=True,
    ),
    # The paid plan's cheapest tier that fits: 8 GB on burstable Graviton.
    SizeTier(
        key="small",
        label="Small",
        instance_type="t4g.large",
        arch=ARCH_ARM64,
        vcpu=2,
        ram_gb=8,
        disk_gb=30,
        approx_usd_per_hr=0.0672,
    ),
    SizeTier(
        key="light",
        label="Light",
        instance_type="t4g.xlarge",
        arch=ARCH_ARM64,
        vcpu=4,
        ram_gb=16,
        disk_gb=40,
        approx_usd_per_hr=0.1344,
    ),
    SizeTier(
        key="balanced",
        label="Development",
        instance_type="m7g.2xlarge",
        arch=ARCH_ARM64,
        vcpu=8,
        ram_gb=32,
        disk_gb=60,
        approx_usd_per_hr=0.326,
        recommended=True,
    ),
    SizeTier(
        key="power",
        label="Power",
        instance_type="m7g.4xlarge",
        arch=ARCH_ARM64,
        vcpu=16,
        ram_gb=64,
        disk_gb=80,
        approx_usd_per_hr=0.653,
    ),
    # x86_64 lane — same shapes, for users who need Intel/AMD.
    SizeTier(
        key="light-x86",
        label="Light (x86_64)",
        instance_type="t3.xlarge",
        arch=ARCH_X86_64,
        vcpu=4,
        ram_gb=16,
        disk_gb=40,
        approx_usd_per_hr=0.166,
    ),
    SizeTier(
        key="balanced-x86",
        label="Development (x86_64)",
        instance_type="m7i.2xlarge",
        arch=ARCH_X86_64,
        vcpu=8,
        ram_gb=32,
        disk_gb=60,
        approx_usd_per_hr=0.403,
    ),
    SizeTier(
        key="power-x86",
        label="Power (x86_64)",
        instance_type="m7i.4xlarge",
        arch=ARCH_X86_64,
        vcpu=16,
        ram_gb=64,
        disk_gb=80,
        approx_usd_per_hr=0.806,
    ),
)

TIERS_BY_KEY: dict[str, SizeTier] = {t.key: t for t in _TIERS}

# Per-region prices for the tiers the first-run home card offers, so its figures
# match the region the home is built in: us-east-1, plus the three regions AWS's
# newest sign-up pins a new account to (local_signin.HOME_REGION_CANDIDATES).
# On-demand Linux USD per hour from AWS's public price list, the data behind
# https://aws.amazon.com/ec2/pricing/on-demand/ ; gp3 storage USD per GB-month
# from https://aws.amazon.com/ebs/pricing/ . Refresh both from there. Display
# only, like approx_usd_per_hr.
PRICE_FALLBACK_REGION = "us-east-1"
ON_DEMAND_USD_PER_HR: dict[str, dict[str, float]] = {
    "us-east-1": {
        "t4g.small": 0.0168,
        "t4g.medium": 0.0336,
        "t4g.large": 0.0672,
        "t4g.xlarge": 0.1344,
        "m7i-flex.large": 0.09576,
    },
    "us-east-2": {
        "t4g.small": 0.0168,
        "t4g.medium": 0.0336,
        "t4g.large": 0.0672,
        "t4g.xlarge": 0.1344,
        "m7i-flex.large": 0.09576,
    },
    "eu-north-1": {
        "t4g.small": 0.0172,
        "t4g.medium": 0.0344,
        "t4g.large": 0.0688,
        "t4g.xlarge": 0.1376,
        "m7i-flex.large": 0.10175,
    },
    "ap-southeast-2": {
        "t4g.small": 0.0212,
        "t4g.medium": 0.0424,
        "t4g.large": 0.0848,
        "t4g.xlarge": 0.1696,
        "m7i-flex.large": 0.1197,
    },
}
GP3_USD_PER_GB_MONTH: dict[str, float] = {
    "us-east-1": 0.08,
    "us-east-2": 0.08,
    "eu-north-1": 0.0836,
    "ap-southeast-2": 0.096,
}

DEFAULT_TIER_KEY = "balanced"

# The tiers only the first-run home card offers (``setup_cards.HOME_PLAN_SIZES``).
# That card renders each tier's facts from its payload, so the Settings launcher,
# which keeps its own copy of the other tiers' shapes, does not list them.
HOME_CARD_ONLY_TIER_KEYS = ("lite", "economy", "small", "starter")

# The tiers offered in the interactive picker, in display order (the x86 lane is
# reachable via --size but kept out of the default 3-choice menu for simplicity).
INTERACTIVE_TIER_KEYS = ("light", "balanced", "power")


def all_tiers() -> tuple[SizeTier, ...]:
    """All defined tiers (arm64 lane first, then x86)."""
    return _TIERS


def interactive_tiers() -> list[SizeTier]:
    """The tiers shown in the wizard's size picker."""
    return [TIERS_BY_KEY[k] for k in INTERACTIVE_TIER_KEYS]


def get_tier(key: str) -> SizeTier:
    """Resolve a tier by key; raise ``KeyError`` with the valid set if unknown."""
    try:
        return TIERS_BY_KEY[key]
    except KeyError:
        valid = ", ".join(TIERS_BY_KEY)
        raise KeyError(f"unknown size '{key}' (choose one of: {valid})") from None


def default_tier() -> SizeTier:
    """The recommended default tier."""
    return TIERS_BY_KEY[DEFAULT_TIER_KEY]


def region_prices(tier: SizeTier, region: str) -> tuple[float, float, str]:
    """*tier*'s USD/hour and gp3 USD/GB-month in *region*, and the region they are from.

    A region or type not in the table gets the PRICE_FALLBACK_REGION figures, and
    a type not in that either gets the tier's own ``approx_usd_per_hr``.
    """
    for where in (region, PRICE_FALLBACK_REGION):
        hourly = ON_DEMAND_USD_PER_HR.get(where, {}).get(tier.instance_type)
        if hourly is not None:
            return hourly, GP3_USD_PER_GB_MONTH[where], where
    return tier.approx_usd_per_hr, GP3_USD_PER_GB_MONTH[PRICE_FALLBACK_REGION], ""


def monthly_estimate(tier: SizeTier, hours_per_day: float = 24.0) -> float:
    """Approximate USD/month for a tier at the given daily uptime (display only)."""
    return round(tier.approx_usd_per_hr * hours_per_day * 30, 2)
