"""The ``connect`` card: a curated connection, granted on the provider's consent page."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kiro_crew import setup_cards as sc
from kiro_crew.setup_actions.base import SetupAction

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState


def _validate(args: dict[str, Any]) -> dict[str, Any]:
    return {"provider": sc.validate_slug(args.get("provider"))}


async def _build(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._build_connect(args)


async def _commit(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._commit_connect(state, card, input_)


ACTION = SetupAction(
    kind=sc.KIND_CONNECT,
    commit=_commit,
    title=lambda card: f"Connect {card.payload.get('provider', {}).get('name', '')}".strip(),
    summary="a curated connection such as github",
    arguments={"provider": {"type": "string", "description": "registry slug"}},
    validate=_validate,
    build=_build,
    result_detail=lambda card: " The connection is granted; its tools load in the next session.",
)
