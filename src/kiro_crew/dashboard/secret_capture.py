"""Move credentials a user pasted into chat out of the message.

A user message is the one row the transcript stores as typed, and it is the
first thing the model reads, so a pasted token would otherwise sit in both
forever. Before the message is queued, written or sent, every credential the
shared detectors recognise is replaced by a ``secret://NAME`` reference. For
the dashboard owner the value is stored in the vault under that name; for any
other caller it is only removed. A deterministic notice row says what happened.

AWS keys and private keys are removed but never stored: Kiro Crew does not hold
AWS credentials (the aws CLI resolves them from the user's own profile), and a
private key belongs in the user's own key store, not in a chat-fed vault.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from kiro_crew.dashboard.system_notices import SECRET_CAPTURED_KIND
from kiro_crew.sel import sel

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState, _ChatSlot

logger = logging.getLogger(__name__)

#: Vault name per detector rule. Unlisted rules store as ``PASTED_SECRET``.
_NAME_FOR_RULE: dict[str, str] = {
    "github_token": "GITHUB_TOKEN",
    "gitlab_token": "GITLAB_TOKEN",
    "slack_token": "SLACK_TOKEN",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "discord_bot_token": "DISCORD_BOT_TOKEN",
    "stripe_key": "STRIPE_API_KEY",
    "sendgrid_key": "SENDGRID_API_KEY",
    "openai_key": "OPENAI_API_KEY",
    "anthropic_key": "ANTHROPIC_API_KEY",
    "npm_token": "NPM_TOKEN",
    "pypi_token": "PYPI_TOKEN",
    "digitalocean_token": "DIGITALOCEAN_TOKEN",
    "google_oauth_secret": "GOOGLE_OAUTH_SECRET",
    "jwt": "PASTED_JWT",
}
#: Detector rules whose value is removed from the chat but never stored.
_NEVER_STORED: frozenset[str] = frozenset(
    {"aws_access_key_id", "aws_secret_access_key", "aws_session_token", "private_key"}
)
_DEFAULT_NAME = "PASTED_SECRET"
_MAX_SUFFIX = 50


def _vault():  # type: ignore[no-untyped-def]
    from kiro_crew.config.paths import config_dir
    from kiro_crew.secrets.vault import SecretVault

    return SecretVault(config_dir())


async def _store(value: str, base: str) -> str:
    """Store *value* under *base* (or a free ``base_N``); return the name used."""
    import asyncio

    vault = _vault()
    names = set(await asyncio.to_thread(vault.list_names))
    for n in range(1, _MAX_SUFFIX + 1):
        name = base if n == 1 else f"{base}_{n}"
        if name in names:
            existing = await asyncio.to_thread(vault.get, name)
            if existing is not None and existing.reveal() == value:
                return name
            continue
        await vault.set(name, value)
        return name
    raise RuntimeError("no free vault name for a pasted secret")


async def capture_pasted_secrets(
    state: "DashboardState",
    slot: "_ChatSlot",
    message: str,
    *,
    store: bool,
) -> str:
    """Return *message* with pasted credentials replaced by vault references."""
    from kiro_crew.security.redaction import (
        CREDENTIAL_REDACTION_TAGS,
        redact_credentials_with_records,
    )

    cleaned, _warnings, matches = redact_credentials_with_records(message)
    if not matches:
        return message
    replacement: dict[int, str] = {}
    stored: list[str] = []
    removed_only = 0
    for match in matches:
        if store and match.rule not in _NEVER_STORED and match.value.strip():
            try:
                name = await _store(
                    match.value.strip(), _NAME_FOR_RULE.get(match.rule, _DEFAULT_NAME)
                )
            except Exception:
                logger.warning("storing a pasted secret failed; removing it only", exc_info=True)
                removed_only += 1
                continue
            replacement[match.ordinal] = f"secret://{name}"
            stored.append(name)
        else:
            removed_only += 1
    out: list[str] = []
    cursor = 0
    ordinal = 0
    while True:
        hits = [(cleaned.find(tag, cursor), tag) for tag in CREDENTIAL_REDACTION_TAGS]
        hits = [(pos, tag) for pos, tag in hits if pos >= 0]
        if not hits:
            out.append(cleaned[cursor:])
            break
        pos, tag = min(hits)
        out.append(cleaned[cursor:pos])
        out.append(replacement.get(ordinal, tag))
        cursor = pos + len(tag)
        ordinal += 1
    rewritten = "".join(out)
    parts: list[str] = []
    if stored:
        refs = ", ".join(f"secret://{n}" for n in stored)
        parts.append(
            f"I moved {len(stored)} pasted secret(s) out of this chat into the vault: {refs}. "
            "The chat keeps only the reference."
        )
    if removed_only:
        parts.append(
            f"I removed {removed_only} pasted credential(s) from this chat without storing them. "
            "AWS keys and private keys stay in your own profile or key store."
        )
    slot.append("assistant", " ".join(parts), "msg msg-system", meta={"kind": SECRET_CAPTURED_KIND})
    sel().log_api_access(
        caller="dashboard",
        operation="chat.secret_capture",
        outcome="ok",
        source="dashboard",
        resources=f"slot={slot.key} stored={len(stored)} removed={removed_only}",
    )
    return rewritten
