"""The ``service`` card: keep Kiro Crew running when the browser closes."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from kiro_crew import setup_cards as sc
from kiro_crew.setup_actions.base import SetupAction

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState


async def _build(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from kiro_crew.dashboard import setup_flow as sf

    return await asyncio.to_thread(sf._service_payload), {}


async def _commit(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._commit_service(state, card, input_)


ACTION = SetupAction(
    kind=sc.KIND_SERVICE,
    commit=_commit,
    title=lambda card: "Keep Kiro Crew running",
    summary="keep running when the browser closes",
    build=_build,
)
