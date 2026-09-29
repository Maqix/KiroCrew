"""The shape of one setup action: what a setup-card kind declares about itself.

``dashboard/setup_flow.py`` reads these fields instead of branching on the kind,
and ``mcp_tools/setup.py`` builds the ``setup_card`` tool's schema from them. See
the package docstring for the rules an action module keeps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Mapping

from kiro_crew.setup_cards import SetupCard

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState

#: Governance scope every setup card is checked against (propose AND commit), with
#: the card kind as the item its inner ``kinds`` ruleset matches.
SETUP_SCOPE = "capabilities.setup"

#: ``(state, card, input)`` → the card after the owner's click did its work.
Committer = Callable[["DashboardState", SetupCard, dict[str, Any]], Awaitable[SetupCard]]
#: A proposal's arguments → ``(payload, private)``: what the owner is shown, and
#: the server-only state the commit reads.
Builder = Callable[[dict[str, Any]], Awaitable[tuple[dict[str, Any], dict[str, Any]]]]


def _no_arguments(args: dict[str, Any]) -> dict[str, Any]:
    return {}


@dataclass(frozen=True)
class Decision:
    """A decision a kind offers beside ``commit`` and ``decline``.

    *claimed* decides who guards it. ``True``: ``setup_flow.decide`` re-checks
    governance, claims the card against the posted hash, and runs *run* as a
    :data:`Committer` exactly as it runs a commit (the cron card's ``preview``).
    ``False``: *run* is the whole decision, called as
    ``run(state, card, card_hash, input, same_machine=...)``, and does its own
    governance check and hash-bound claim (the home card's ``aws_signin`` and
    ``region``).
    """

    run: Callable[..., Awaitable[SetupCard]]
    #: What ``decide`` answers (``invalid_decision``) when a card of another kind
    #: is sent this decision.
    refusal: str
    claimed: bool = True


@dataclass(frozen=True)
class SetupAction:
    """One setup-card kind, self-described."""

    kind: str
    #: What the owner's ``commit`` click does, after the flow claimed the card.
    commit: Committer
    #: The card's heading in what the MODEL reads: its transcript row, the
    #: proposal's confirmation and the ``[Setup card result]`` turn. The
    #: dashboard titles the card from its own catalog.
    title: Callable[[SetupCard], str]
    #: Whether the agent may propose it with ``setup_card``. ``False`` is a
    #: gateway-only kind (the privacy disclosure): it is absent from the tool's
    #: schema, the MCP tool refuses it, and ``propose`` answers it as unknown.
    proposable: bool = True
    #: This kind's clause in the ``setup_card`` tool description.
    summary: str = ""
    #: The ``setup_card`` properties this kind reads: JSON-schema fragments whose
    #: ``description`` is this kind's own, merged by ``setup_card_properties``.
    arguments: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: Checks a proposal inside the MCP server, so the model gets a precise error
    #: before it is told a card exists, and returns the directive's arguments
    #: (without ``kind``). ``build`` validates again: it is the side that acts.
    validate: Callable[[dict[str, Any]], dict[str, Any]] = _no_arguments
    #: Builds a proposal's card in the gateway. Required when *proposable*.
    build: Builder | None = None
    #: Extra decisions by name (each also in ``setup_cards.DECISIONS``).
    decisions: Mapping[str, Decision] = field(default_factory=dict)
    #: Runs after a ``decline`` is recorded (the cron card removes its preview job).
    on_decline: Callable[["DashboardState", SetupCard], Awaitable[None]] | None = None
    #: Runs right after a click claims the card, before its committer, with
    #: whether the owner's request came straight from this machine.
    on_claim: Callable[[SetupCard, bool], Awaitable[SetupCard]] | None = None
    #: The committed card's detail sentence in the ``[Setup card result]`` turn.
    result_detail: Callable[[SetupCard], str] | None = None
    #: Runs after the ``[Setup card result]`` turn is dispatched, for what must
    #: appear AFTER it in the chat (the home question after a kept job: shown
    #: before the result, the tray read it as a card the chat had moved past).
    after_report: Callable[["DashboardState", SetupCard], Awaitable[None]] | None = None
    #: Every governance scope the kind answers to. ``capabilities.setup`` always
    #: applies (the flow checks it for every kind); a further scope needs *vet*.
    scopes: tuple[str, ...] = (SETUP_SCOPE,)
    #: The further scopes' check, ``session_key`` → a refusal reason or ``None``.
    vet: Callable[[str], str | None] | None = None
    #: ``False`` only for a gateway-only kind whose commit a policy may not
    #: refuse: the privacy acknowledgement, without which nothing else runs.
    governed: bool = True
    #: Whether a decided card is reported to the agent as a ``[Setup card
    #: result]`` turn (the privacy commit starts the first model turn itself).
    reported: bool = True
    #: Decided on its own schedule: a pending card of this kind does not hold
    #: other proposals back ("one decision at a time"), nor is it held back.
    stack_exempt: bool = False
    #: A committed card of this kind lifts the card budget (the first kept job).
    lifts_budget: bool = False
    #: Whether a card of this kind is one the gateway showed on its own rather
    #: than the agent's proposal (the home card of the first run's own step).
    gateway_card: Callable[[SetupCard], bool] | None = None

    def is_gateway_card(self, card: SetupCard) -> bool:
        """The gateway's own step, not the agent's: outside its budget and its stack."""
        if not self.proposable:
            return True
        return self.gateway_card is not None and self.gateway_card(card)
