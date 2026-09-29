"""The ``profile`` card: the agent's name, the reply language, timezone and the user's profile."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kiro_crew import setup_cards as sc
from kiro_crew.setup_actions.base import SetupAction

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState


async def _build(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    return sc.build_profile(args), {}


async def _commit(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._commit_profile(state, card, input_)


def _result_detail(card: sc.SetupCard) -> str:
    return " Saved: " + ", ".join((card.outcome or {}).get("applied", [])) + "."


ACTION = SetupAction(
    kind=sc.KIND_PROFILE,
    commit=_commit,
    title=lambda card: "Save your profile",
    summary="agent name, language, timezone, technical level, role",
    arguments={
        "fields": {
            "type": "object",
            "description": (
                "any of bot_name, language (BCP-47), timezone (IANA), "
                "technical_level (" + "|".join(sorted(sc.TECHNICAL_LEVELS)) + "), "
                "role (" + "|".join(sorted(sc.USER_ROLES)) + ")"
            ),
        },
    },
    validate=sc.build_profile,
    build=_build,
    result_detail=_result_detail,
)
