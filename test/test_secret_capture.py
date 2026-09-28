"""Pasted credentials leave a chat message before anything stores or sends it."""

from __future__ import annotations

import pytest

from kiro_crew.config.paths import config_dir
from kiro_crew.dashboard.secret_capture import capture_pasted_secrets
from kiro_crew.dashboard.system_notices import SECRET_CAPTURED_KIND
from kiro_crew.secrets.vault import SecretVault

GITHUB_TOKEN = "ghp_" + "A1b2C3d4E5" * 3 + "abcdef"
AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"


class _Slot:
    key = "chat-1-1"

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, dict | None]] = []

    def append(self, role, content, cls="", ts="", *, broadcast=True, meta=None):
        self.rows.append((role, content, meta))


@pytest.mark.asyncio
async def test_an_owner_paste_is_vaulted_and_replaced_by_a_reference():
    slot = _Slot()
    out = await capture_pasted_secrets(
        None, slot, f"use this token {GITHUB_TOKEN} for my repos", store=True
    )
    assert GITHUB_TOKEN not in out
    assert "secret://GITHUB_TOKEN" in out
    assert SecretVault(config_dir()).get("GITHUB_TOKEN").reveal() == GITHUB_TOKEN
    role, notice, meta = slot.rows[-1]
    assert role == "assistant" and meta == {"kind": SECRET_CAPTURED_KIND}
    assert "secret://GITHUB_TOKEN" in notice and GITHUB_TOKEN not in notice


@pytest.mark.asyncio
async def test_the_same_value_reuses_its_name_and_a_new_value_gets_a_suffix():
    await capture_pasted_secrets(None, _Slot(), GITHUB_TOKEN, store=True)
    again = await capture_pasted_secrets(None, _Slot(), GITHUB_TOKEN, store=True)
    assert "secret://GITHUB_TOKEN" in again
    other = "ghp_" + "Z9y8X7w6V5" * 3 + "zyxwvu"
    third = await capture_pasted_secrets(None, _Slot(), other, store=True)
    assert "secret://GITHUB_TOKEN_2" in third


@pytest.mark.asyncio
async def test_a_non_owner_paste_is_removed_but_not_stored():
    slot = _Slot()
    out = await capture_pasted_secrets(None, slot, f"token {GITHUB_TOKEN}", store=False)
    assert GITHUB_TOKEN not in out and "secret://" not in out
    assert SecretVault(config_dir()).list_names() == []
    assert "without storing" in slot.rows[-1][1]


@pytest.mark.asyncio
async def test_aws_keys_are_never_stored():
    slot = _Slot()
    out = await capture_pasted_secrets(None, slot, f"key {AWS_KEY}", store=True)
    assert AWS_KEY not in out
    assert SecretVault(config_dir()).list_names() == []


@pytest.mark.asyncio
async def test_a_message_without_credentials_is_untouched():
    slot = _Slot()
    text = "please connect github and watch my PRs"
    assert await capture_pasted_secrets(None, slot, text, store=True) == text
    assert slot.rows == []
