"""What the AWS CLI on this machine already knows: its sign-in and region."""

from __future__ import annotations

import pytest

from kiro_crew.cloud import aws, local_signin


@pytest.mark.parametrize(
    ("which", "result", "expected"),
    [
        (None, (0, "", ""), None),
        ("/usr/bin/aws", (255, "", "Unable to locate credentials"), None),
        ("/usr/bin/aws", (0, '{"Account": "not-an-id"}', ""), None),
        ("/usr/bin/aws", (0, "not json", ""), None),
        (
            "/usr/bin/aws",
            (0, '{"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:root"}', ""),
            ("123456789012", "arn:aws:iam::123456789012:root"),
        ),
    ],
)
def test_detection_reads_one_sts_identity(monkeypatch, which, result, expected) -> None:
    calls: list = []
    monkeypatch.setattr(local_signin, "aws_cli_present", lambda: which is not None)
    monkeypatch.setattr(
        aws, "run_aws", lambda argv, profile, region, **kw: calls.append(argv) or result
    )
    found = local_signin.detect("me")
    assert ((found.account, found.arn) if found else None) == expected
    assert calls == ([["sts", "get-caller-identity", "--output", "json"]] if which else [])


def test_the_cli_is_found_outside_path_like_every_aws_spawn(monkeypatch) -> None:
    from kiro_crew.deploy import engine

    monkeypatch.setattr(engine, "resolve_aws_bin", lambda: "/usr/local/bin/aws")
    monkeypatch.setattr(local_signin.shutil, "which", lambda name: None)
    assert local_signin.aws_cli_present() is True
    monkeypatch.setattr(engine, "resolve_aws_bin", lambda: "aws")
    assert local_signin.aws_cli_present() is False


def test_the_account_is_shown_by_its_last_four_digits() -> None:
    signin = local_signin.AwsSignIn("123456789012", "arn:aws:sts::123456789012:assumed-role/D/me")
    assert signin.account_hint == "…9012"
    assert signin.long_lived_keys is False
    assert local_signin.AwsSignIn("123456789012", "arn:aws:iam::1:user/me").long_lived_keys


def test_the_profile_region_is_read_from_the_config_file(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config"
    config.write_text("[profile work]\nregion = eu-north-1\n[default]\nregion = bogus\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    assert local_signin.configured_region("work") == "eu-north-1"
    assert local_signin.configured_region("") == ""
    assert local_signin.configured_region("missing") == ""
