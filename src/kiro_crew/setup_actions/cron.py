"""The ``cron`` card: a scheduled job the owner previews once before keeping it.

``preview`` creates the job disabled and silent and runs it once; ``commit``
(Keep it) turns it on; a decline removes the preview job. Keeping the first job
lifts the card budget. A job card also answers to ``capabilities.cron``, the gate
every other way of authoring a job passes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kiro_crew import setup_cards as sc
from kiro_crew.setup_actions.base import SETUP_SCOPE, Decision, SetupAction

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState

#: The job-authoring capability a cron card answers to beside ``capabilities.setup``.
CRON_SCOPE = "capabilities.cron"
#: The arguments a valid proposal carries into the directive.
_DIRECTIVE_KEYS = ("name", "prompt", "cron_expr", "every_secs", "timezone")


def _validate(args: dict[str, Any]) -> dict[str, Any]:
    sc.build_cron(args)
    return {k: args[k] for k in _DIRECTIVE_KEYS if args.get(k) not in (None, "")}


async def _build(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    return sc.build_cron(args)


async def _commit(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._commit_cron(state, card, input_)


async def _after_report(state: "DashboardState", card: sc.SetupCard) -> None:
    from kiro_crew.dashboard import setup_flow as sf

    await sf.offer_home_after_job(state, card)


async def _preview(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    return await sf._preview_cron(state, card, input_)


async def _on_decline(state: "DashboardState", card: sc.SetupCard) -> sc.SetupCard:
    from kiro_crew.dashboard import setup_flow as sf

    await sf._discard_preview_job(state, card)
    return await sf.prepare_home_after_job(state, card)


def _vet(session_key: str) -> str | None:
    from kiro_crew.mcp_cron import _vet_cron_capability_governance

    err = _vet_cron_capability_governance(session_key)
    return err.removeprefix("Error: ") if err else None


def _result_detail(card: sc.SetupCard) -> str:
    outcome = card.outcome or {}
    job_id = outcome.get("job_id", "")
    detail = (
        f" The job is kept (job id {job_id}) and runs {card.payload.get('schedule_human', '')}."
    )
    return detail


ACTION = SetupAction(
    kind=sc.KIND_CRON,
    commit=_commit,
    title=lambda card: str(card.payload.get("name", "Scheduled job")),
    summary="a scheduled job; the user previews one run before keeping it",
    arguments={
        "name": {"type": "string", "description": "job name"},
        "prompt": {"type": "string", "description": "what each run does"},
        "cron_expr": {"type": "string", "description": "5-field expression"},
        "every_secs": {
            "type": "integer",
            "description": f"interval, >= {sc.CRON_MIN_EVERY_SECS}",
        },
        "timezone": {"type": "string", "description": "IANA timezone"},
    },
    validate=_validate,
    build=_build,
    decisions={
        sc.DECISION_PREVIEW: Decision(
            run=_preview, refusal="only a scheduled-job card has a preview"
        ),
    },
    on_decline=_on_decline,
    result_detail=_result_detail,
    after_report=_after_report,
    scopes=(SETUP_SCOPE, CRON_SCOPE),
    vet=_vet,
    lifts_budget=True,
)
