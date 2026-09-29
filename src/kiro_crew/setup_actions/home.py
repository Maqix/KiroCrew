"""The ``home`` card: a permanent home in the owner's own AWS account.

One card carries the whole journey: Sign in to AWS (``aws_signin``), the region
when AWS names none (``region``), Build, the home's own Kiro sign-in, then Move
in. Its build runs in the background, so a pending home card neither holds other
proposals back nor is held back by one. The first run shows one on its own right
after privacy (payload ``offer``), which is the gateway's step, not the agent's.
The flow is ``dashboard/setup_flow.py``, ``setup_aws_signin.py``,
``home_signin.py`` and ``setup_move_in.py``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from kiro_crew import setup_cards as sc
from kiro_crew.setup_actions.base import Decision, SetupAction

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState

#: Payload flag of the home card the gateway shows as the first run's own step.
HOME_STEP_KEY = "offer"


def _validate(args: dict[str, Any]) -> dict[str, Any]:
    return sc.build_home(args)


async def _build(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from kiro_crew.dashboard import setup_flow as sf

    return await asyncio.to_thread(sf._home_payload, sc.build_home(args))


async def _commit(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._commit_home(state, card, input_)


async def _aws_signin(
    state: "DashboardState",
    card: sc.SetupCard,
    card_hash: str,
    input_: dict[str, Any],
    *,
    same_machine: bool,
) -> sc.SetupCard:
    from kiro_crew.dashboard.setup_aws_signin import decide_signin

    return await decide_signin(state, card, card_hash, input_, same_machine=same_machine)


async def _region(
    state: "DashboardState",
    card: sc.SetupCard,
    card_hash: str,
    input_: dict[str, Any],
    *,
    same_machine: bool,
) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._decide_home_region(state, card, card_hash, input_)


async def _on_claim(card: sc.SetupCard, same_machine: bool) -> sc.SetupCard:
    # Whether a build started by this click may open the home's Kiro sign-in
    # page in the owner's browser (``home_signin``).
    from kiro_crew.dashboard.home_signin import record_browser_here

    return await record_browser_here(card, same_machine)


def _result_detail(card: sc.SetupCard) -> str:
    outcome = card.outcome or {}
    if outcome.get("moved") and not outcome.get("simulated"):
        from kiro_crew.dashboard.setup_move_in import result_detail

        return result_detail(outcome)
    return (
        " The crew moved into its home in the cloud; keep helping the user from here."
        if outcome.get("moved")
        else " The home is ready."
    )


ACTION = SetupAction(
    kind=sc.KIND_HOME,
    commit=_commit,
    title=lambda card: "Your home in the cloud",
    summary=(
        "a permanent home in the user's own AWS account, built in the background; the "
        "card shows the monthly cost and the user starts the build"
    ),
    arguments={
        "region": {"type": "string", "description": "AWS region"},
        "profile": {"type": "string", "description": "AWS CLI profile"},
        "size": {
            "type": "string",
            "description": f"size key, default {sc.HOME_DEFAULT_SIZE}",
        },
    },
    validate=_validate,
    build=_build,
    decisions={
        sc.DECISION_AWS_SIGNIN: Decision(
            run=_aws_signin, refusal="only a home card signs in to AWS", claimed=False
        ),
        sc.DECISION_REGION: Decision(
            run=_region, refusal="this card does not ask for a region", claimed=False
        ),
    },
    on_claim=_on_claim,
    result_detail=_result_detail,
    stack_exempt=True,
    gateway_card=lambda card: bool(card.payload.get(HOME_STEP_KEY)),
)
