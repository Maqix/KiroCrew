"""What this machine's AWS CLI already knows: its sign-in and its default region.

The first-run chat offers a home in the cloud only when the AWS CLI here is
signed in, and names the region the profile uses. Both answers come from the
AWS CLI itself: one read-only ``sts get-caller-identity`` call, and the
``region`` line of ``~/.aws/config``. Kiro Crew never reads the credentials file
and never sees a credential; it keeps only the account id and the caller's ARN
the CLI prints.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

#: Where to create an AWS account, for a user who has none. Account creation
#: cannot be automated: it asks for an email and a sign-in or card check.
SIGNUP_URL = "https://signin.aws.amazon.com/signup?request_type=register"
#: The same sign-up for someone who already has an AWS Builder ID: it creates the
#: account with that identity, so there is no new password.
BUILDER_ID_SIGNUP_URL = "https://signin.aws.amazon.com/signup?request_type=builderId"
#: How long the one ``kiro-cli whoami`` that picks the sign-up page may take.
_WHOAMI_TIMEOUT_SECS = 10.0

_REGION_RE = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-\d$")
_ACCOUNT_RE = re.compile(r"^\d{12}$")


@dataclass(frozen=True)
class AwsSignIn:
    """Who the AWS CLI profile signs in as: the account and the caller's ARN."""

    account: str
    arn: str

    @property
    def long_lived_keys(self) -> bool:
        # An IAM user's profile holds access keys; a role or `aws login` session
        # holds short-lived credentials. Told from the ARN, never from the files.
        return ":user/" in self.arn

    @property
    def account_hint(self) -> str:
        """The account id with only its last four digits shown."""
        return "…" + self.account[-4:]


def signup_url(builder_id: bool) -> str:
    """The AWS account sign-up page: the Builder ID one when *builder_id*."""
    return BUILDER_ID_SIGNUP_URL if builder_id else SIGNUP_URL


def kiro_signs_in_with_builder_id() -> bool:
    """Whether this machine's Kiro sign-in is exactly AWS Builder ID.

    One bounded, read-only ``kiro-cli whoami``. A social or Identity Center
    sign-in, no sign-in, and an unknown answer are all False: they get the plain
    sign-up.
    """
    from kiro_crew.cloud.login_target import ACCOUNT_TYPE_BUILDER_ID, discover_local_identity

    try:
        identity = discover_local_identity(timeout=_WHOAMI_TIMEOUT_SECS)
    except Exception:
        return False
    if not identity:
        return False
    return str(identity.get("account_type") or "") == ACCOUNT_TYPE_BUILDER_ID


def aws_cli_present() -> bool:
    """Whether the AWS CLI is installed, found the way every ``aws`` spawn finds it.

    The shared resolver also looks outside ``PATH``, so a gateway launched from
    the GUI with a minimal ``PATH`` sees the same CLI ``run_aws`` would run.
    """
    from kiro_crew.deploy.engine import resolve_aws_bin

    found = resolve_aws_bin()
    return os.path.isabs(found) or shutil.which(found) is not None


def detect(profile: str = "", *, timeout: int = 10) -> AwsSignIn | None:
    """The sign-in the AWS CLI already has for *profile*, from one read-only STS call."""
    if not aws_cli_present():
        return None
    try:
        from kiro_crew.cloud import aws

        rc, out, _err = aws.run_aws(
            ["sts", "get-caller-identity", "--output", "json"], profile, "", timeout=timeout
        )
        if rc != 0:
            return None
        ident = json.loads(out or "{}")
    except Exception:
        return None
    account = str(ident.get("Account") or "")
    if not _ACCOUNT_RE.match(account):
        return None
    return AwsSignIn(account=account, arn=str(ident.get("Arn") or ""))


def configured_region(profile: str = "") -> str:
    """The region the profile names in ``~/.aws/config``, or ``""``.

    Accounts made through the newest AWS sign-up are pinned to one region by
    country, so the profile's own region beats any default Kiro Crew picks.
    Only the config file is read, never the credentials file.
    """
    path = Path(os.environ.get("AWS_CONFIG_FILE") or (Path.home() / ".aws" / "config"))
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error):
        return ""
    section = f"profile {profile}" if profile else "default"
    region = parser.get(section, "region", fallback="").strip()
    return region if _REGION_RE.match(region) else ""


# ── the account a home is built in: its region, its plan, its vCPU quota ──────

#: Where AWS's newest sign-up pins a new account, by the owner's country. Tried in
#: this order when the profile's own region refuses.
HOME_REGION_CANDIDATES: tuple[str, ...] = ("us-east-2", "eu-north-1", "ap-southeast-2")
#: The regions a home can be built in, which the home card offers when no region
#: answers: the commercial regions enabled on every account by default, where the
#: EC2 template's Amazon Linux 2023 AMI alias and Session Manager both exist. A
#: region that needs an opt-in is left out. A size the chosen region does not
#: offer is refused at launch (``ec2.discover_network``).
HOME_REGIONS: tuple[str, ...] = (
    "us-east-1",
    "us-east-2",
    "us-west-1",
    "us-west-2",
    "ca-central-1",
    "sa-east-1",
    "eu-west-1",
    "eu-west-2",
    "eu-west-3",
    "eu-central-1",
    "eu-north-1",
    "ap-south-1",
    "ap-northeast-1",
    "ap-northeast-2",
    "ap-northeast-3",
    "ap-southeast-1",
    "ap-southeast-2",
)
#: The Free plan's API answers in us-east-1 only.
_PLAN_REGION = "us-east-1"
#: EC2's "Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances" vCPU quota.
VCPU_QUOTA_CODE = "L-1216C47A"
#: Seconds any one of these read-only calls may take.
_PROBE_TIMEOUT_SECS = 20
PLAN_FREE = "FREE"
PLAN_PAID = "PAID"
PLAN_UNKNOWN = "unknown"
#: What one region probe found: the account builds there, refuses it, or nothing is known.
REGION_OK = "ok"
REGION_REFUSED = "refused"
REGION_UNKNOWN = "unknown"


def _denied(err: str) -> bool:
    from kiro_crew.cloud import aws

    return aws.is_access_denied(err) or "AuthFailure" in err or "OptInRequired" in err


def probe_region(profile: str, region: str) -> str:
    """Whether this account answers in *region*: REGION_OK, REGION_REFUSED or REGION_UNKNOWN.

    One read-only ``ec2 describe-availability-zones``. An access refusal is
    REGION_REFUSED (a new sign-up account refuses every region but its own); any
    other failure is REGION_UNKNOWN.
    """
    from kiro_crew.cloud import aws

    if not _REGION_RE.match(region or ""):
        return REGION_UNKNOWN
    try:
        rc, _out, err = aws.run_aws(
            ["ec2", "describe-availability-zones", "--output", "json"],
            profile,
            region,
            timeout=_PROBE_TIMEOUT_SECS,
        )
    except Exception:
        return REGION_UNKNOWN
    if rc == 0:
        return REGION_OK
    return REGION_REFUSED if _denied(err) else REGION_UNKNOWN


def resolve_home_region(profile: str, preferred: str = "") -> str:
    """The region this account can build in, or ``""`` when none answers.

    :func:`probe_region` per region: *preferred*, then the profile's own region,
    then :data:`HOME_REGION_CANDIDATES`. Only a refusal moves on to the next
    region; anything else unknown means nothing is known.
    """
    seen: list[str] = []
    for region in (preferred, configured_region(profile), *HOME_REGION_CANDIDATES):
        if not region or region in seen or not _REGION_RE.match(region):
            continue
        seen.append(region)
        found = probe_region(profile, region)
        if found == REGION_OK:
            return region
        if found != REGION_REFUSED:
            return ""
    return ""


def account_plan(profile: str, region: str = _PLAN_REGION) -> dict[str, object]:
    """The account's AWS plan: ``{type, credits_usd?, expires?}``. Read-only.

    ``type`` is ``FREE``, ``PAID`` or ``unknown``. An account older than the
    plans has no plan state (``ResourceNotFoundException``) and is PAID. This
    never changes the plan: upgrading is the owner's, on AWS's own page.
    """
    from kiro_crew.cloud import aws

    try:
        rc, out, err = aws.run_aws(
            ["freetier", "get-account-plan-state", "--output", "json"],
            profile,
            region or _PLAN_REGION,
            timeout=_PROBE_TIMEOUT_SECS,
        )
    except Exception:
        return {"type": PLAN_UNKNOWN}
    if rc != 0:
        if "ResourceNotFoundException" in err:
            return {"type": PLAN_PAID}
        if region and region != _PLAN_REGION:
            return account_plan(profile, _PLAN_REGION)
        return {"type": PLAN_UNKNOWN}
    try:
        state = json.loads(out or "{}")
    except json.JSONDecodeError:
        return {"type": PLAN_UNKNOWN}
    kind = str(state.get("accountPlanType") or "").upper()
    if kind not in (PLAN_FREE, PLAN_PAID):
        return {"type": PLAN_UNKNOWN}
    plan: dict[str, object] = {"type": kind}
    credits = state.get("accountPlanRemainingCredits")
    if isinstance(credits, dict) and str(credits.get("unit") or "").upper() == "USD":
        amount = credits.get("amount")
        if isinstance(amount, (int, float, str)):
            try:
                plan["credits_usd"] = round(float(amount), 2)
            except ValueError:
                pass
    expires = state.get("accountPlanExpirationDate")
    if isinstance(expires, str) and expires:
        plan["expires"] = expires[:40]
    return plan


def vcpu_quota(profile: str, region: str) -> int | None:
    """EC2's on-demand standard vCPU quota in *region*, or ``None`` when unknown."""
    from kiro_crew.cloud import aws

    try:
        rc, out, _err = aws.run_aws(
            [
                "service-quotas",
                "get-service-quota",
                "--service-code",
                "ec2",
                "--quota-code",
                VCPU_QUOTA_CODE,
                "--output",
                "json",
            ],
            profile,
            region,
            timeout=_PROBE_TIMEOUT_SECS,
        )
        if rc != 0:
            return None
        value = json.loads(out or "{}").get("Quota", {}).get("Value")
        return int(float(value)) if value is not None else None
    except Exception:
        return None
