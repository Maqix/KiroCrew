"""The ``channel`` card: a messaging channel's bot token, then a ``/pair`` code.

The commit, the pairing code and its checks are ``dashboard/setup_channel.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kiro_crew import setup_cards as sc
from kiro_crew.setup_actions.base import SetupAction

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState


def _validate(args: dict[str, Any]) -> dict[str, Any]:
    return {"channel": sc.build_channel(args)["channel"]}


async def _build(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    return sc.build_channel(args), {}


async def _commit(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._commit_channel(state, card, input_)


def _result_detail(card: sc.SetupCard) -> str:
    who = str((card.outcome or {}).get("username") or "") or "the user"
    return f" Paired: {who} can now message the bot and reach you there."


ACTION = SetupAction(
    kind=sc.KIND_CHANNEL,
    commit=_commit,
    title=lambda card: f"Connect {card.payload.get('label', 'a channel')}",
    summary=(
        f"a {' or '.join(sorted(sc.CHANNELS.values()))} bot the user reaches you through; "
        "they type its token into the card and pair their account with a one-time code"
    ),
    arguments={"channel": {"type": "string", "enum": sorted(sc.CHANNELS)}},
    validate=_validate,
    build=_build,
    result_detail=_result_detail,
)
