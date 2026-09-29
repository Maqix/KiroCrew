"""The ``credential`` card: the owner types a secret into the card; it goes to the vault.

The value travels in the decide request only and reaches the vault and nowhere
else (SC2); the agent learns only ``secret://NAME``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from kiro_crew import setup_cards as sc
from kiro_crew.setup_actions.base import SetupAction

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState


async def _build(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = sc.build_credential(args)
    from kiro_crew.config.paths import config_dir
    from kiro_crew.secrets.vault import SecretVault

    names = await asyncio.to_thread(lambda: SecretVault(config_dir()).list_names())
    payload["exists"] = payload["name"] in names
    return payload, {}


async def _commit(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._commit_credential(state, card, input_)


def _result_detail(card: sc.SetupCard) -> str:
    ref = (card.outcome or {}).get("ref", "")
    return f" Reference it as {ref}; you never see the value."


ACTION = SetupAction(
    kind=sc.KIND_CREDENTIAL,
    commit=_commit,
    title=lambda card: f"Store {card.payload.get('name', 'a secret')}",
    summary="the user types a secret into the card; you get only secret://NAME",
    arguments={
        "name": {"type": "string", "description": "UPPER_SNAKE vault name"},
        "purpose": {"type": "string", "description": "why it is needed"},
        "hosts": {"type": "array", "items": {"type": "string"}},
    },
    validate=sc.build_credential,
    build=_build,
    result_detail=_result_detail,
)
