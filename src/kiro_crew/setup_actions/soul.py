"""The ``soul`` card: the agent's ``SOUL.md`` persona or the user's ``USER.md`` notes."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from kiro_crew import setup_cards as sc
from kiro_crew.setup_actions.base import SetupAction

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState


def _validate(args: dict[str, Any]) -> dict[str, Any]:
    built = sc.build_soul(args, None)
    return {"file": built["file"], "content": built["content"]}


async def _build(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    # The card shows the file it replaces, read when it is proposed.
    file = args.get("file", "SOUL")
    previous = await asyncio.to_thread(sc.read_persona, file) if file in sc.SOUL_FILES else None
    return sc.build_soul(args, previous), {}


async def _commit(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._commit_soul(state, card, input_)


ACTION = SetupAction(
    kind=sc.KIND_SOUL,
    commit=_commit,
    title=lambda card: f"Save {card.payload.get('file', 'SOUL')}.md",
    summary=f"SOUL.md persona or USER.md notes, <={sc.SOUL_MAX_CHARS} chars",
    arguments={
        "file": {"type": "string", "enum": list(sc.SOUL_FILES)},
        "content": {"type": "string", "description": "the full file"},
    },
    validate=_validate,
    build=_build,
)
