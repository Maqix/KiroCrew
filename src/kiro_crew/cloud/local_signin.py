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
