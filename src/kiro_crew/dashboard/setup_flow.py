"""Dashboard half of setup cards and the one-chat first run.

Three entry points:

* :func:`propose` — the ``setup_card`` session directive's applier. The agent
  asks for a card; the gateway validates the request, builds the payload the
  owner will see, stores the card, and appends an inline row to the chat.
* :func:`decide` — the owner's click (``POST /api/setup/cards/{id}/decide``).
  This is the ONLY path that commits anything, and it re-checks the owner, the
  payload hash, the card's status and governance before it does.
* :func:`ensure_first_run_session` — at gateway start, a fresh install gets one
  pinned chat whose first row is the deterministic privacy card. Acknowledging
  it dispatches the first model turn (:func:`start_first_run_turn`).

A decided card is reported back to the agent as a ``[Setup card result]``
envelope turn so the conversation continues. That turn carries user provenance:
it exists because the owner clicked, which is the same authenticated-human fact
a typed message carries.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from kiro_crew import setup_cards as sc
from kiro_crew.first_run import mark_stage, read_first_run_slot, record_slot
from kiro_crew.sel import sel

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState, _ChatSlot

logger = logging.getLogger(__name__)

#: Owner-only websocket event carrying ``{slot, card}`` on every card change.
SETUP_CARD_EVENT = "setup_card_update"
#: How long a connect card waits for the provider's consent before expiring.
_CONNECT_WAIT_SECS = 600
_CONNECT_POLL_SECS = 3.0
#: Longest cron preview text kept on the card.
_PREVIEW_MAX_CHARS = 6000
#: Governance scope every setup card is checked against (propose AND commit).
SETUP_SCOPE = "capabilities.setup"

#: Background tasks this module owns (a connect card's consent watcher), held
#: so they are not garbage collected mid-flight.
_tasks: set[asyncio.Task[Any]] = set()


def _audit(operation: str, outcome: str, session_key: str, resources: str) -> None:
    sel().log_api_access(
        caller="setup_card",
        operation=operation,
        outcome=outcome,
        source="dashboard",
        resources=f"{resources} session:{session_key}",
    )


def _governance_denial(kind: str, session_key: str) -> str | None:
    """Return a refusal reason when governance forbids a *kind* setup card."""
    try:
        from kiro_crew.platform.governance_profiles import governance_permits

        decision = governance_permits(SETUP_SCOPE, kind, session_key=session_key, log_warning=False)
    except Exception:
        logger.warning("setup-card governance check failed; refusing", exc_info=True)
        return "the governance check could not complete"
    if not getattr(decision, "permitted", True):
        return str(getattr(decision, "reason", "") or "blocked by your security policy")
    if kind == sc.KIND_CRON:
        from kiro_crew.mcp_cron import _vet_cron_capability_governance

        err = _vet_cron_capability_governance(session_key)
        if err:
            return err.removeprefix("Error: ")
    return None


# ── broadcasting and transcript rows ────────────────────────────────────────


def broadcast(state: "DashboardState", card: sc.SetupCard) -> None:
    state.broadcast_ws_owners(SETUP_CARD_EVENT, {"slot": card.slot, "card": card.public()})


def _card_title(card: sc.SetupCard) -> str:
    p = card.payload
    if card.kind == sc.KIND_CONNECT:
        return f"Connect {p.get('provider', {}).get('name', '')}".strip()
    if card.kind == sc.KIND_CRON:
        return str(p.get("name", "Scheduled job"))
    if card.kind == sc.KIND_CREDENTIAL:
        return f"Store {p.get('name', 'a secret')}"
    if card.kind == sc.KIND_CHANNEL:
        return f"Connect {p.get('label', 'a channel')}"
    if card.kind == sc.KIND_SOUL:
        return f"Save {p.get('file', 'SOUL')}.md"
    return {
        sc.KIND_PRIVACY: "Privacy",
        sc.KIND_PROFILE: "Save your profile",
        sc.KIND_IMPORT: "Bring your setup over",
        sc.KIND_SERVICE: "Keep Kiro Crew running",
        sc.KIND_HOME: "Your home in the cloud",
    }.get(card.kind, card.kind)


def _append_card_row(state: "DashboardState", slot: "_ChatSlot", card: sc.SetupCard) -> None:
    """Add the inline row the frontend renders the card at.

    The row's text is what the MODEL sees of the card on replay; the browser
    ignores it and renders from ``GET /api/setup/cards/{id}``. No ``injectKind``:
    a card row opens no turn.
    """
    summary = f"(Setup card shown to the user: {_card_title(card)} [{card.kind}])"
    slot.append(
        "inject", summary, "msg msg-inject", meta={"setupCard": {"id": card.id, "kind": card.kind}}
    )
    state.push_slots_update()


def _result_text(card: sc.SetupCard) -> str:
    from kiro_crew.dashboard.state import SETUP_RESULT_END, SETUP_RESULT_PREFIX

    outcome = card.outcome or {}
    detail = ""
    if card.status == sc.STATUS_COMMITTED:
        if card.kind == sc.KIND_CRON:
            detail = f" The job is kept (job id {outcome.get('job_id', '')}) and runs {card.payload.get('schedule_human', '')}."
        elif card.kind == sc.KIND_CREDENTIAL:
            detail = f" Reference it as {outcome.get('ref', '')}; you never see the value."
        elif card.kind == sc.KIND_IMPORT:
            detail = (
                f" Imported {outcome.get('imported_count', 0)} items; "
                f"{outcome.get('jobs_added_disabled', 0)} imported jobs were added DISABLED "
                "for the user to review."
            )
            jobs = [j for j in outcome.get("jobs") or [] if isinstance(j, dict)]
            if jobs:
                listed = "; ".join(
                    f"{j.get('name', '')} ({j.get('schedule', '')}): {j.get('prompt', '')}"
                    for j in jobs
                )
                detail += (
                    f" The imported jobs: {listed}. To keep one, propose a cron card with the "
                    "prompt adapted to this install; there is no need to look them up."
                )
        elif card.kind == sc.KIND_CONNECT:
            detail = " The connection is granted; its tools load in the next session."
        elif card.kind == sc.KIND_CHANNEL:
            who = str(outcome.get("username") or "") or "the user"
            detail = f" Paired: {who} can now message the bot and reach you there."
        elif card.kind == sc.KIND_PROFILE:
            detail = " Saved: " + ", ".join(outcome.get("applied", [])) + "."
        elif card.kind == sc.KIND_HOME and outcome.get("moved") and not outcome.get("simulated"):
            from kiro_crew.dashboard.setup_move_in import result_detail

            detail = result_detail(outcome)
        elif card.kind == sc.KIND_HOME:
            detail = (
                " The crew moved into its home in the cloud; keep helping the user from here."
                if outcome.get("moved")
                else " The home is ready."
            )
    elif card.status in (sc.STATUS_FAILED, sc.STATUS_EXPIRED) and card.error:
        detail = f" Reason: {card.error.get('message', '')}"
    return (
        f'{SETUP_RESULT_PREFIX} {card.kind} card "{_card_title(card)}": {card.status}.{detail}\n'
        "Continue the setup conversation from here; do not re-propose a card the user declined.\n"
        f"{SETUP_RESULT_END}"
    )


async def _dispatch_envelope_turn(
    state: "DashboardState", slot: "_ChatSlot", text: str, inject_kind: str
) -> None:
    """Start (or queue) a turn carrying a gateway envelope with user provenance."""
    from kiro_crew.dashboard.chat_runner import _run_chat  # lazy: import cycle
    from kiro_crew.dashboard.turn_dispatch import spawn_guarded_turn

    if slot.running or getattr(slot, "_in_stage_execution", False):
        qid = slot.queue_append(text, kind=inject_kind, directive_user_origin=True)
        slot.append("queued", text, f'{{"queue_id": "{qid}"}}')
        state.push_slots_update()
        return
    slot.append("inject", text, "msg msg-inject", meta={"injectKind": inject_kind})
    task = spawn_guarded_turn(
        state,
        slot,
        _run_chat(state, slot, text, _synthetic_payload=True, _directive_user_origin=True),
    )
    slot.task = task
    state.push_slots_update()


async def _report(state: "DashboardState", card: sc.SetupCard) -> None:
    """Tell the agent how a card was decided, in the card's own chat."""
    slot = state.get_slot(card.slot)
    if slot is None:
        return
    await _dispatch_envelope_turn(state, slot, _result_text(card), "setup_result")


# ── propose ─────────────────────────────────────────────────────────────────


async def propose(
    state: "DashboardState",
    slot: "_ChatSlot",
    session_key: str,
    args: dict[str, Any],
    *,
    producer_is_user_facing: bool,
) -> str:
    """Apply a ``setup_card`` directive; return the model-facing confirmation."""
    kind = str(args.get("kind", ""))
    if kind not in sc.PROPOSABLE_KINDS:
        return f"Error: unknown setup card kind {kind!r}. Nothing was shown."
    if not producer_is_user_facing:
        # SC8: a cron, a watch, an injected event or a sub-agent must not put a
        # setup decision in front of the user; only a turn a person started, or
        # one that exists because they clicked a card, may.
        _audit("setup_card.propose", "denied", session_key, f"kind:{kind} reason=not_user_facing")
        return (
            "Error: setup cards can only be shown in a turn the user started. "
            "Nothing was shown; ask the user in your reply instead."
        )
    denial = await asyncio.to_thread(_governance_denial, kind, session_key)
    if denial:
        _audit("setup_card.propose", "denied", session_key, f"kind:{kind} reason=governance")
        return f"Error: this setup action is blocked by policy ({denial}). Nothing was shown."
    from kiro_crew.dashboard.setup_guardrails import quota_paused

    if quota_paused(slot.key):
        return (
            "Error: this chat ran out of model allowance on an earlier turn, so setup "
            "cards are paused until a turn completes. Nothing was shown; tell the user "
            "in one line that you can reply again and ask whether to carry on with setup."
        )
    existing = await asyncio.to_thread(sc.list_cards, slot.key)
    # The gateway's own steps (privacy, the home step) are not the agent's proposals.
    proposable = [
        c for c in existing if c.kind != sc.KIND_PRIVACY and not c.payload.get(HOME_STEP_KEY)
    ]
    kept_job = any(c.kind == sc.KIND_CRON and c.status == sc.STATUS_COMMITTED for c in existing)
    if not kept_job and len(proposable) >= sc.CARD_BUDGET_BEFORE_FIRST_JOB:
        return (
            f"Error: this chat already showed {len(proposable)} setup cards without a kept job. "
            "Stop proposing setup steps; help the user with what they asked instead."
        )
    try:
        payload, private = await _build(state, slot, kind, args)
    except sc.CardRejected as exc:
        return f"Error: {exc} Nothing was shown."
    digest = sc.payload_hash(kind, payload)
    for card in existing:
        if card.status == sc.STATUS_PENDING and card.payload_hash == digest:
            return f"That setup card is already showing ({_card_title(card)}). End your turn."
    # One decision at a time: a second card while one is still waiting splits the
    # user's attention and buries the first. The home card is exempt -- it builds
    # in the background by design and is decided on its own schedule.
    waiting = next(
        (
            c
            for c in existing
            if c.status == sc.STATUS_PENDING and c.kind not in (sc.KIND_HOME, sc.KIND_PRIVACY)
        ),
        None,
    )
    if waiting is not None and kind != sc.KIND_HOME:
        return (
            f"Error: the user has not decided the card already showing ({_card_title(waiting)}). "
            "One card at a time: end your turn and let them decide it first."
        )
    card = await asyncio.to_thread(
        lambda: sc.create_card(
            slot=slot.key, session_key=session_key, kind=kind, payload=payload, private=private
        )
    )
    _append_card_row(state, slot, card)
    broadcast(state, card)
    _audit("setup_card.propose", "ok", session_key, f"kind:{kind} card:{card.id}")
    return (
        f"Setup card shown: {_card_title(card)}. Nothing changes until the user clicks it. "
        "End your turn now; the decision arrives as a [Setup card result] message."
    )


async def _build(
    state: "DashboardState", slot: "_ChatSlot", kind: str, args: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    if kind == sc.KIND_PROFILE:
        return sc.build_profile(args), {}
    if kind == sc.KIND_SOUL:
        file = args.get("file", "SOUL")
        previous = await asyncio.to_thread(sc.read_persona, file) if file in sc.SOUL_FILES else None
        return sc.build_soul(args, previous), {}
    if kind == sc.KIND_CRON:
        return sc.build_cron(args)
    if kind == sc.KIND_CREDENTIAL:
        payload = sc.build_credential(args)
        from kiro_crew.config.paths import config_dir
        from kiro_crew.secrets.vault import SecretVault

        names = await asyncio.to_thread(lambda: SecretVault(config_dir()).list_names())
        payload["exists"] = payload["name"] in names
        return payload, {}
    if kind == sc.KIND_CHANNEL:
        return sc.build_channel(args), {}
    if kind == sc.KIND_CONNECT:
        return await _build_connect(args)
    if kind == sc.KIND_IMPORT:
        return await _build_import(args)
    if kind == sc.KIND_SERVICE:
        return await asyncio.to_thread(_service_payload), {}
    if kind == sc.KIND_HOME:
        return await asyncio.to_thread(_home_payload, sc.build_home(args))
    raise sc.CardRejected(f"unknown setup card kind {kind!r}", "unknown_kind")


async def _build_connect(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from kiro_crew.connections.registry import get_provider, get_visible_providers
    from kiro_crew.dashboard.handlers.connections import _oauth_client_configured

    slug = sc.validate_slug(args.get("provider"))
    provider = get_provider(slug)
    visible = {str(p["slug"]) for p in get_visible_providers()}
    if provider is None or slug not in visible:
        names = ", ".join(sorted(visible))
        raise sc.CardRejected(f"{slug!r} is not a curated connection; choose one of: {names}.")
    configured = await asyncio.to_thread(_oauth_client_configured, provider)
    payload = {
        "provider": {
            "slug": slug,
            "name": str(provider.get("name", slug)),
            "category": str(provider.get("category", "")),
        },
        "needs_client_config": not configured,
    }
    return payload, {"mcp_url": str(provider["mcp_url"])}


def _import_plan() -> dict[str, Any]:
    from kiro_crew.onboarding_import import preview_import

    return preview_import()


async def _build_import(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from kiro_crew.dashboard.handlers.onboarding_import import _scan_response

    plan = await asyncio.to_thread(_import_plan)
    projected = _scan_response(plan)
    wanted = args.get("source_ids")
    sources = []
    for source in projected.get("sources", []):
        if wanted and source.get("id") not in wanted:
            continue
        categories = [
            {"id": c["id"], "label": c["label"], "count": int(c.get("count", 0))}
            for c in source.get("categories", [])
            if int(c.get("count", 0)) > 0
        ]
        if categories:
            sources.append({"id": source["id"], "name": source["name"], "categories": categories})
    if not sources:
        raise sc.CardRejected("there is nothing to bring over from another agent on this machine.")
    return {"sources": sources, "jobs": []}, {}


def _service_serves_this_home() -> bool:
    from kiro_crew.config.paths import _default_home, _valid_override_home

    override = _valid_override_home()
    if override is None:
        return True
    try:
        return override.resolve() == _default_home().resolve()
    except OSError:
        return False


def _service_payload() -> dict[str, Any]:
    from kiro_crew.service import controller
    from kiro_crew.service.common import Platform

    platform = controller.current_platform()
    if platform == Platform.SYSTEMD:
        kind = "linux"
    elif platform == Platform.LAUNCHD:
        kind = "macos"
    else:
        kind = "unsupported"
    # The unit is machine-wide and runs the DEFAULT data home, so it keeps this
    # crew running only when this crew is that home.
    installed = controller.installed_unit_path() is not None and _service_serves_this_home()
    # On Linux the unit is a system unit written with sudo, and the running
    # gateway holds the data-home lock the service's gateway needs, so the owner
    # runs one terminal line that stops this gateway first.
    command = (
        "kirocrew stop && kirocrew service install"
        if kind == "linux"
        else "kirocrew service install"
    )
    return {
        "platform": kind,
        "command": command,
        "needs_terminal": kind != "macos",
        "installed": installed,
    }


def _home_payload(
    settings: dict[str, Any], *, picked: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """What the home card shows before the owner agrees to spend money.

    *picked* is what :func:`local_signin.probe_region` found for a region the
    owner chose on the card (``settings["region"]``): that region is kept, and
    no other is tried.
    """
    from kiro_crew.cloud import local_signin
    from kiro_crew.cloud.simulated_engine import simulation_enabled
    from kiro_crew.cloud.sizes import get_tier

    tier = get_tier(settings["size"])
    simulated = simulation_enabled()
    signed_in = True
    account = ""
    if not simulated:
        try:
            from kiro_crew.cloud.iam import reachability_check

            probe = reachability_check(settings["profile"], settings["region"])
            signed_in = bool(probe.get("reachable"))
            raw = str(probe.get("account") or "")
            account = ("…" + raw[-4:]) if raw else ""
        except Exception:
            logger.warning("AWS reachability probe failed", exc_info=True)
            signed_in = False
    plan: dict[str, Any] | None = None
    region_unknown = False
    if not simulated and signed_in:
        # The account's own region (a new sign-up account works in one region
        # only) and its AWS plan (the Free plan launches Starter only).
        if picked is not None:
            _none, plan = _account_facts(settings["profile"], settings["region"], find_region=False)
            region_unknown = picked != local_signin.REGION_OK
        else:
            resolved, plan = _account_facts(settings["profile"], settings["region"])
            if resolved:
                settings = {**settings, "region": resolved}
            else:
                # No region answered: the card asks the owner, starting from the
                # region the profile names.
                region_unknown = True
                own = local_signin.configured_region(settings["profile"])
                if own in local_signin.HOME_REGIONS:
                    settings = {**settings, "region": own}
    options, default_size = sc.home_size_options(plan, settings["region"])
    payload = {
        "provider": {"id": "aws_ec2", "label": "Your AWS account"},
        "simulated": simulated,
        "region": settings["region"],
        "profile": settings["profile"] or "default",
        "size": {
            "key": tier.key,
            "label": tier.label,
            "instance_type": tier.instance_type,
            "ram_gb": tier.ram_gb,
            "vcpu": tier.vcpu,
        },
        "monthly_usd": sc.monthly_estimate_usd(tier.key, settings["region"]),
        "billed_by": "AWS, to your own account",
        "aws_signed_in": signed_in,
        "aws_account": account,
        "sign_in_commands": ["aws login"],
        "size_options": options,
        "size_default": default_size,
    }
    if plan is not None:
        payload["plan"] = plan
    if region_unknown:
        payload["region_unknown"] = True
        payload["region_choices"] = list(local_signin.HOME_REGIONS)
    if not simulated and not signed_in:
        # A machine with no AWS sign-in may have no AWS account either: the card
        # links the sign-up (the Builder ID one when Kiro signs in with Builder
        # ID) and says when the AWS CLI is missing. A signed-in machine's payload
        # stays as it is, and runs no whoami.
        builder_id = local_signin.kiro_signs_in_with_builder_id()
        payload["signup_url"] = local_signin.signup_url(builder_id)
        payload["signup_builder_id"] = builder_id
        payload["aws_cli_installed"] = local_signin.aws_cli_present()
    return payload, {"settings": settings, "phase": "build"}


def _account_facts(
    profile: str, preferred_region: str, *, find_region: bool = True
) -> tuple[str, dict[str, Any]]:
    """The account's buildable region and its AWS plan, asked side by side (read-only).

    Without *find_region* only the plan is asked, and the region is ``""``.
    """
    from concurrent.futures import ThreadPoolExecutor

    from kiro_crew.cloud import local_signin

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="home-facts") as pool:
        region = (
            pool.submit(local_signin.resolve_home_region, profile, preferred_region)
            if find_region
            else None
        )
        plan = pool.submit(local_signin.account_plan, profile)
        try:
            resolved = region.result() if region is not None else ""
        except Exception:
            logger.warning("home region probe failed", exc_info=True)
            resolved = ""
        try:
            found = plan.result()
        except Exception:
            logger.warning("AWS plan probe failed", exc_info=True)
            found = {"type": local_signin.PLAN_UNKNOWN}
    return resolved, found


async def _reissue_home(
    card: sc.SetupCard, settings: dict[str, Any], *, picked: str | None = None
) -> sc.SetupCard:
    """Recompute a pending home card's payload for *settings* and show it under a new hash."""
    payload, private = await asyncio.to_thread(_home_payload, settings, picked=picked)
    if card.payload.get(HOME_STEP_KEY):
        payload[HOME_STEP_KEY] = True
    return await asyncio.to_thread(
        sc.replace_payload, card.id, payload, settings=private["settings"]
    )


async def refresh_home_payload(card: sc.SetupCard) -> sc.SetupCard:
    """Recompute a pending home card's payload once AWS answers for its profile.

    After a sign-in from the card, the region, the plan and the size options are
    known; the owner sees them, under a new hash, before Build. The card is
    returned unchanged when it moved on or the recompute fails.
    """
    try:
        return await _reissue_home(card, sc.build_home(dict(card.private.get("settings") or {})))
    except Exception:
        logger.warning("home card %s: payload refresh failed", card.id, exc_info=True)
        return card


async def _decide_home_region(
    state: "DashboardState", card: sc.SetupCard, card_hash: str, input_: dict[str, Any]
) -> sc.SetupCard:
    """The home card's ``region`` decision: the owner names the account's region.

    Offered only while no region answered (``payload.region_unknown``). The pick
    is checked on the server (:func:`sc.validate_home_region`), probed read-only,
    and the card re-issued for it under a new hash, with that region's prices. A
    pick that does not answer either stays the owner's to build in, and the card
    keeps its picker and says so.
    """
    import hmac

    from kiro_crew.cloud import local_signin

    if card.kind != sc.KIND_HOME or card.payload.get("region_unknown") is not True:
        raise sc.CardRejected("this card does not ask for a region", "invalid_decision")
    if card.status != sc.STATUS_PENDING:
        raise sc.CardRejected("this card is not waiting for a decision", "card_not_pending")
    if not hmac.compare_digest(str(card_hash), card.payload_hash):
        raise sc.CardRejected("this card changed since it was shown", "card_hash_mismatch")
    denial = await asyncio.to_thread(_governance_denial, card.kind, card.session_key)
    if denial:
        raise sc.CardRejected(f"blocked by policy: {denial}", "governance_denied")
    region = sc.validate_home_region(input_.get("region"))
    settings = {**sc.build_home(dict(card.private.get("settings") or {})), "region": region}
    answer = await asyncio.to_thread(local_signin.probe_region, settings["profile"], region)
    card = await _reissue_home(card, settings, picked=answer)
    if answer == local_signin.REGION_OK:
        card = await _finish(card, sc.STATUS_PENDING)
    else:
        card = await _back_to_pending(
            card,
            "home_region_no_answer",
            f"AWS did not answer for this account in {region} either; build there if it "
            "is the region your AWS console shows, or pick another",
        )
    broadcast(state, card)
    _audit("setup_card.region", answer, card.session_key, f"kind:home card:{card.id}")
    return card


# ── decide ──────────────────────────────────────────────────────────────────


async def decide(
    state: "DashboardState",
    card_id: str,
    decision: str,
    card_hash: str,
    input_: dict[str, Any],
    *,
    same_machine: bool = False,
) -> sc.SetupCard:
    """Apply the owner's *decision* on *card_id*. Raises :class:`CardRejected`.

    *same_machine* is whether the owner's request came straight from this
    machine; only the home card reads it (its AWS sign-in, and whether its build
    may open the home's Kiro sign-in page here).
    """
    if decision not in sc.DECISIONS:
        raise sc.CardRejected(
            "decision must be commit, decline, preview, aws_signin or region", "invalid_decision"
        )
    found = await asyncio.to_thread(sc.get_card, card_id)
    if found is None:
        raise sc.CardRejected("setup card not found", "card_not_found")
    card: sc.SetupCard = found
    if decision == sc.DECISION_AWS_SIGNIN:
        from kiro_crew.dashboard.setup_aws_signin import decide_signin

        return await decide_signin(state, card, card_hash, input_, same_machine=same_machine)
    if decision == sc.DECISION_REGION:
        return await _decide_home_region(state, card, card_hash, input_)
    if decision == sc.DECISION_DECLINE:
        card = await asyncio.to_thread(
            sc.claim_pending, card_id, card_hash, to_status=sc.STATUS_DECLINED
        )
        card = await _finish(card, sc.STATUS_DECLINED)
        if card.kind == sc.KIND_CRON:
            await _discard_preview_job(state, card)
        broadcast(state, card)
        _audit(
            "setup_card.decide", "declined", card.session_key, f"kind:{card.kind} card:{card.id}"
        )
        if card.kind != sc.KIND_PRIVACY:
            await _report(state, card)
        return card
    if decision == sc.DECISION_PREVIEW and card.kind != sc.KIND_CRON:
        raise sc.CardRejected("only a scheduled-job card has a preview", "invalid_decision")
    denial = await asyncio.to_thread(_governance_denial, card.kind, card.session_key)
    if denial and card.kind != sc.KIND_PRIVACY:
        raise sc.CardRejected(f"blocked by policy: {denial}", "governance_denied")
    card = await asyncio.to_thread(sc.claim_pending, card_id, card_hash)
    if card.kind == sc.KIND_HOME:
        from kiro_crew.dashboard.home_signin import record_browser_here

        card = await record_browser_here(card, same_machine)
    broadcast(state, card)
    committer = _PREVIEWERS if decision == sc.DECISION_PREVIEW else _COMMITTERS
    try:
        card = await committer[card.kind](state, card, input_)
    except sc.CardRejected as exc:
        card = await _back_to_pending(card, exc.code, str(exc))
    except Exception:
        logger.exception("setup card %s (%s) failed", card.id, card.kind)
        card = await _finish(
            card, sc.STATUS_FAILED, error=("commit_failed", "Something went wrong.")
        )
    broadcast(state, card)
    _audit("setup_card.decide", card.status, card.session_key, f"kind:{card.kind} card:{card.id}")
    if card.terminal and card.kind != sc.KIND_PRIVACY:
        await _report(state, card)
    return card


async def _finish(
    card: sc.SetupCard,
    status: str,
    *,
    outcome: dict[str, Any] | None = None,
    error: tuple[str, str] | None = None,
) -> sc.SetupCard:
    def _mutate(c: sc.SetupCard) -> None:
        c.status = status
        if outcome is not None:
            c.outcome = outcome
        c.error = {"code": error[0], "message": error[1]} if error else None
        if status in sc.TERMINAL_STATUSES:
            c.decided_ts = time.time()

    return await asyncio.to_thread(sc.update_card, card.id, _mutate)


async def _back_to_pending(card: sc.SetupCard, code: str, message: str) -> sc.SetupCard:
    """A recoverable failure: the card waits for the owner again, error shown."""
    return await _finish(card, sc.STATUS_PENDING, error=(code, message))


# ── committers ──────────────────────────────────────────────────────────────


async def _update_config(mutate: Any) -> None:
    from kiro_crew.config.loader import config_path, update_config_locked
    from kiro_crew.dashboard.chat_utils import run_config_write
    from kiro_crew.dashboard.handlers.core import _hot_apply_after_write

    def _write() -> None:
        def _apply(data: dict[str, Any]) -> dict[str, Any]:
            mutate(data)
            return data

        update_config_locked(config_path(), mutate=_apply, stamp_meta=False)

    await run_config_write(_write)
    await _hot_apply_after_write()


async def _commit_privacy(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    telemetry = input_.get("telemetry")

    def _mutate(data: dict[str, Any]) -> None:
        data.setdefault("dashboard", {})["privacy_acked"] = True
        if telemetry is False:
            data.setdefault("telemetry", {})["beacon_enabled"] = False

    await _update_config(_mutate)
    await asyncio.to_thread(mark_stage, "privacy")
    card = await _finish(card, sc.STATUS_COMMITTED, outcome={})
    slot = state.get_slot(card.slot)
    if slot is not None:
        await _offer_home_step(state, slot, card.session_key)
        await start_first_run_turn(state, slot)
    return card


#: Payload flag of the home card the gateway shows as the first run's own step.
HOME_STEP_KEY = "offer"


async def _offer_home_step(state: "DashboardState", slot: "_ChatSlot", session_key: str) -> None:
    """The first run's "Where should your crew live?" step, right after privacy.

    A step of its own, not a sentence in the Hello: a question asked only in the
    agent's prose was missed next to the first card on screen. So the gateway
    shows a home card every first run, signed in to AWS or not. Signed in, it
    states the account, region and monthly cost and builds on one click; not
    signed in, it walks the owner through signing in or creating an AWS account
    first. "Keep it on this machine" declines it. A ``--home cloud`` answer shows
    the same card without the step framing; ``--home here|later`` shows none.

    The state file gates nothing: it only decides whether the card is SHOWN.
    Building still needs the owner's click on the card, which states the cost.
    """
    from kiro_crew.cloud import local_signin
    from kiro_crew.first_run import read_state

    home = read_state().get("home")
    if isinstance(home, dict) and home.get("choice") != "cloud":
        return
    existing = await asyncio.to_thread(sc.list_cards, slot.key)
    if any(c.kind == sc.KIND_HOME for c in existing):
        return
    chosen = isinstance(home, dict)
    try:
        wanted: dict[str, Any] = dict(home) if isinstance(home, dict) else {}
        if not wanted.get("region"):
            wanted["region"] = local_signin.configured_region(str(wanted.get("profile") or ""))
        settings = sc.build_home(wanted)
        payload, private = await asyncio.to_thread(_home_payload, settings)
    except sc.CardRejected:
        logger.warning("first-run home step is invalid; not offering a home card")
        return
    if not chosen:
        payload[HOME_STEP_KEY] = True
    card = await asyncio.to_thread(
        lambda: sc.create_card(
            slot=slot.key,
            session_key=session_key,
            kind=sc.KIND_HOME,
            payload=payload,
            private=private,
        )
    )
    _append_card_row(state, slot, card)
    broadcast(state, card)


async def _commit_profile(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    fields = dict(card.payload.get("fields") or {})

    def _mutate(data: dict[str, Any]) -> None:
        dash = data.setdefault("dashboard", {})
        if "bot_name" in fields:
            data.setdefault("agent", {})["bot_name"] = fields["bot_name"]
            dash["bot_name"] = fields["bot_name"]
        if "language" in fields:
            dash["language"] = fields["language"]
        if "timezone" in fields:
            data["timezone"] = fields["timezone"]
        if "technical_level" in fields:
            dash["user_technical_level"] = fields["technical_level"]
        if "role" in fields:
            dash["user_role"] = fields["role"]

    await _update_config(_mutate)
    await asyncio.to_thread(mark_stage, "hello")
    if fields.get("bot_name"):
        await _rename_unnamed_main_chat(state, str(fields["bot_name"]))
    return await _finish(card, sc.STATUS_COMMITTED, outcome={"applied": sorted(fields)})


async def _rename_unnamed_main_chat(state: "DashboardState", name: str) -> None:
    """Give a main chat that graduated before the agent had a name the new name."""
    from kiro_crew.first_run import read_main_slot

    main = await asyncio.to_thread(read_main_slot)
    slot = state.get_slot(main) if main else None
    if slot is None or slot.title != MAIN_CHAT_FALLBACK_TITLE:
        return
    await _set_explicit_title(state, slot, name)
    state.push_slots_update()


async def _set_explicit_title(state: "DashboardState", slot: "_ChatSlot", title: str) -> None:
    """Title *slot* the way a manual rename does, so the auto-titler never replaces it."""
    from kiro_crew.dashboard.chat_title import _persist_title

    slot.title = title
    slot._titled = True
    slot._title_origin = "user"
    slot._title_epoch = int(getattr(slot, "_title_epoch", 0)) + 1
    try:
        await _persist_title(state, slot)
    except Exception:
        logger.warning("main chat title is live but not yet saved for %s", slot.key, exc_info=True)
    push_title = getattr(state, "push_slot_title", None)
    if callable(push_title):
        push_title(slot.key, title)


def _write_persona(file: str, content: str) -> None:
    from kiro_crew.atomic_write import atomic_write

    path = sc.persona_path(file)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, content, mode=0o600)


async def _commit_soul(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    file = str(card.payload["file"])
    await asyncio.to_thread(_write_persona, file, str(card.payload["content"]))
    return await _finish(card, sc.STATUS_COMMITTED, outcome={"file": file})


async def _commit_import(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    from kiro_crew.dashboard.handlers.onboarding_import import run_import_apply

    excluded = {
        (str(e.get("source_id")), str(e.get("category_id")))
        for e in (input_.get("exclude") or [])
        if isinstance(e, dict)
    }
    selected = {
        (str(source["id"]), str(category["id"]))
        for source in card.payload.get("sources", [])
        for category in source.get("categories", [])
    } - excluded
    if not selected:
        raise sc.CardRejected("nothing is selected to bring over", "import_empty")
    source_ids = sorted({pair[0] for pair in selected})
    result = await run_import_apply(state, source_ids, selected, "skip")
    imported = result.get("imported") or {}
    await asyncio.to_thread(mark_stage, "import")
    return await _finish(
        card,
        sc.STATUS_COMMITTED,
        outcome={
            "imported_count": int(result.get("imported_count", 0)),
            "imported": {k: int(v) for k, v in imported.items()},
            "jobs_added_disabled": int(imported.get("schedules", 0)),
            "jobs": _imported_jobs(state, source_ids),
        },
    )


#: Most imported jobs the result names, and how much of each prompt it quotes.
_IMPORTED_JOBS_LISTED = 10
_IMPORTED_PROMPT_CHARS = 160


def _imported_jobs(state: "DashboardState", source_ids: list[str]) -> list[dict[str, str]]:
    """The jobs an import just added, so the agent can adapt them without hunting.

    They were created by the import, not by this chat, so the chat's own
    session-scoped job tools do not list them.
    """
    owners = {f"import:{sid}" for sid in source_ids}
    try:
        jobs = [
            j
            for j in state.crons.list_jobs(include_disabled=True)
            if getattr(j, "created_by", "") in owners
        ]
    except Exception:
        logger.warning("listing imported jobs for the import card failed", exc_info=True)
        return []
    listed = []
    for job in jobs[:_IMPORTED_JOBS_LISTED]:
        sched = job.schedule
        spec: dict[str, Any] = {"every_secs": sched.every_secs} if sched.every_secs else {}
        if sched.cron_expr:
            spec = {"cron_expr": sched.cron_expr}
        prompt = " ".join(str(job.message or "").split())
        if len(prompt) > _IMPORTED_PROMPT_CHARS:
            prompt = prompt[: _IMPORTED_PROMPT_CHARS - 1] + "…"
        listed.append(
            {
                "name": str(job.name),
                "schedule": sc.humanize_schedule(spec) if spec else str(sched.kind),
                "prompt": prompt,
            }
        )
    return listed


async def _commit_credential(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    value = input_.get("value")
    if not isinstance(value, str) or not value.strip():
        raise sc.CardRejected("enter the value to store", "credential_empty")
    from kiro_crew.config.paths import config_dir
    from kiro_crew.secrets.vault import SecretVault

    name = str(card.payload["name"])
    await SecretVault(config_dir()).set(name, value.strip())
    return await _finish(card, sc.STATUS_COMMITTED, outcome={"ref": f"secret://{name}"})


async def _commit_connect(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    from kiro_crew.connections.registry import get_provider
    from kiro_crew.dashboard.handlers.connections import start_provider_mint
    from kiro_crew.dashboard.handlers.mcp_custom import ensure_remote_server

    slug = str(card.payload["provider"]["slug"])
    provider = get_provider(slug)
    if provider is None:
        return await _finish(
            card,
            sc.STATUS_FAILED,
            error=("unknown_provider", "This provider is no longer offered."),
        )
    written = await ensure_remote_server(
        slug, str(card.private.get("mcp_url") or provider["mcp_url"])
    )
    if written in ("invalid", "malformed"):
        return await _finish(
            card,
            sc.STATUS_FAILED,
            error=("mcp_config_unwritable", "The MCP configuration could not be written."),
        )
    started = await start_provider_mint(provider)
    if started.get("conflict"):
        raise sc.CardRejected(
            "this provider needs an OAuth app configured first (Settings → OAuth Apps)",
            str(started["conflict"]),
        )
    card = await _finish(
        card,
        sc.STATUS_WAITING,
        outcome={"state": started.get("state", "minting"), "oauth_url": None},
    )
    task = asyncio.create_task(_watch_connect(state, card.id, slug))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return card


async def _watch_connect(state: "DashboardState", card_id: str, slug: str) -> None:
    """Follow a mint until the provider grants it, it fails, or time runs out."""
    from kiro_crew.connections.mint import expire_dead_holder, pending_mint_for

    deadline = time.monotonic() + _CONNECT_WAIT_SECS
    last_url: str | None = None
    try:
        while time.monotonic() < deadline:
            await asyncio.sleep(_CONNECT_POLL_SECS)
            await expire_dead_holder(slug)
            view = pending_mint_for(slug) or {}
            mint_state = str(view.get("state", "idle"))
            url = view.get("oauth_url") or None
            card = await asyncio.to_thread(sc.get_card, card_id)
            if card is None or card.status != sc.STATUS_WAITING:
                return
            if mint_state == "granted":
                card = await _finish(
                    card, sc.STATUS_COMMITTED, outcome={"state": "granted", "oauth_url": None}
                )
                await asyncio.to_thread(mark_stage, "connect")
                broadcast(state, card)
                await _report(state, card)
                return
            if mint_state in ("failed", "expired"):
                reason = str(view.get("reason") or "the provider did not grant access")
                card = await _finish(card, sc.STATUS_FAILED, error=("connect_failed", reason))
                broadcast(state, card)
                await _report(state, card)
                return
            if url != last_url:
                last_url = url
                card = await _finish(
                    card, sc.STATUS_WAITING, outcome={"state": mint_state, "oauth_url": url}
                )
                broadcast(state, card)
        card = await asyncio.to_thread(sc.get_card, card_id)
        if card is not None and card.status == sc.STATUS_WAITING:
            card = await _finish(
                card,
                sc.STATUS_EXPIRED,
                error=("connect_timeout", "The consent page was not completed in time."),
            )
            broadcast(state, card)
            await _report(state, card)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("connect card %s watcher failed", card_id)


async def _ensure_job(state: "DashboardState", card: sc.SetupCard, *, enabled: bool) -> str:
    """Create the card's job once (from the private copy) and return its id."""
    job_id = str(card.private.get("job_id") or "")
    if job_id:
        return job_id
    schedule = dict(card.private.get("schedule") or {})
    job = await state.crons.add_job_async(
        str(card.payload["name"]),
        str(card.private["prompt"]),
        every_secs=schedule.get("every_secs"),
        cron_expr=schedule.get("cron_expr"),
        created_by="setup_card",
        enabled=enabled,
        # A preview run neither notifies nor opens a cron tab: its output is shown
        # on the card, and "Keep it" turns delivery on.
        silent=not enabled,
        hide_in_chat=not enabled,
        timezone=str(card.payload.get("timezone") or ""),
        session_key=card.session_key,
        persistent_session=True,
    )

    def _remember(c: sc.SetupCard) -> None:
        c.private["job_id"] = job.id

    await asyncio.to_thread(sc.update_card, card.id, _remember)
    card.private["job_id"] = job.id
    return str(job.id)


async def _preview_cron(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    job_id = await _ensure_job(state, card, enabled=False)
    crons = state.crons
    crons.discard_finished_run(job_id)
    if crons.is_running(job_id):
        raise sc.CardRejected("a preview is already running", "preview_running")
    from kiro_crew.dashboard import setup_preview

    # The run's approvals show on the card while it runs (setup_preview).
    watch = setup_preview.ApprovalWatch(state, card.id, job_id).start()
    try:
        task = asyncio.create_task(crons.run_job(job_id))
        crons.attach_run_task(job_id, task)
        await task
    finally:
        approvals = await watch.stop()
    job = await crons.get_job_async(job_id)
    text = str(getattr(job, "last_result", "") or getattr(job, "last_error", "") or "")
    if len(text) > _PREVIEW_MAX_CHARS:
        text = text[: _PREVIEW_MAX_CHARS - 1] + "…"
    # The cron service records "ok" or "error"; the card speaks success/failure/timeout.
    raw = str(getattr(job, "last_status", "") or "ok")
    status = {"ok": "success", "success": "success", "timeout": "timeout"}.get(raw, "failure")
    await asyncio.to_thread(mark_stage, "preview")
    preview = setup_preview.preview_outcome(state, status, text, approvals)
    return await _finish(card, sc.STATUS_PENDING, outcome={"preview": preview})


async def _commit_cron(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    job_id = str(card.private.get("job_id") or "")
    if job_id:
        await state.crons.update_job_async(job_id, silent=False, hide_in_chat=False)
        await state.crons.enable_job_async(job_id, True)
    else:
        job_id = await _ensure_job(state, card, enabled=True)
    await asyncio.to_thread(mark_stage, "job_kept")
    outcome = dict(card.outcome or {})
    outcome["job_id"] = job_id
    card = await _finish(card, sc.STATUS_COMMITTED, outcome=outcome)
    await graduate(state, card.slot)
    return card


async def _discard_preview_job(state: "DashboardState", card: sc.SetupCard) -> None:
    job_id = str(card.private.get("job_id") or "")
    if not job_id:
        return
    try:
        await state.crons.remove_job_async(job_id, actor="setup_card", source="dashboard")
    except Exception:
        logger.warning("could not remove declined preview job %s", job_id, exc_info=True)


async def _commit_service(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    from kiro_crew.service import controller

    if card.payload.get("platform") == "macos":
        rc = await asyncio.to_thread(controller.install_service)
        if rc != 0:
            raise sc.CardRejected(
                "the service could not be installed; see the gateway log", "service_install_failed"
            )
    elif await asyncio.to_thread(controller.installed_unit_path) is None:
        raise sc.CardRejected(
            "the service is not installed yet; run the command in a terminal, then check again",
            "service_not_installed",
        )
    await asyncio.to_thread(mark_stage, "stay_on")
    return await _finish(card, sc.STATUS_COMMITTED, outcome={"installed": True})


async def _commit_home(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    """Two decisions on one card: "Build my home", then "Move in".

    Between them, a home whose build finished without its Kiro sign-in is in
    phase ``signin``: the commit signs it in (:func:`_sign_home_in`).
    """
    phase = str(card.private.get("phase") or "build")
    if phase == "move":
        return await _move_in(state, card)
    if phase == "signin":
        return await _sign_home_in(state, card)
    from kiro_crew.cloud.simulated_engine import SimulatedLaunchEngine, simulation_enabled
    from kiro_crew.dashboard.handlers_cloud import start_launch_job

    settings = dict(card.private.get("settings") or {})
    if not card.payload.get("simulated"):
        from kiro_crew.cloud.iam import reachability_check

        probe = await asyncio.to_thread(
            reachability_check, settings.get("profile", ""), settings["region"]
        )
        if not probe.get("reachable"):
            raise sc.CardRejected(
                "sign in to AWS first: run `aws login` in a terminal (AWS CLI 2.32+; "
                "`aws sso login` if you use IAM Identity Center), then press the button again",
                "aws_not_signed_in",
            )
    # The region and size the owner saw and chose: the payload is hash-bound,
    # the private settings are not.
    settings["region"] = str(card.payload.get("region") or settings["region"])
    settings["size"] = await _chosen_home_size(card, input_, settings)

    def _chosen(c: sc.SetupCard) -> None:
        c.private["settings"] = dict(settings)

    await asyncio.to_thread(sc.update_card, card.id, _chosen)
    if simulation_enabled() and getattr(state, "cloud_launch_engine", None) is None:
        state.cloud_launch_engine = SimulatedLaunchEngine()
    login_target = None if card.payload.get("simulated") else await _inherited_login_target()
    if login_target is not None and login_target.is_identity_center and not login_target.region:
        # A job carrying this target could never be read back (from_dict refuses
        # an un-regioned Identity Center target), so the card would fail while
        # the worker went on building. Refuse before anything is spent.
        raise sc.CardRejected(
            "this computer signs in to Kiro through IAM Identity Center, but its "
            "region could not be read; sign in again with kiro-cli, then press the "
            "button again",
            "home_identity_region_unknown",
        )
    job, refusal = await start_launch_job(
        state,
        provisioner_id="aws_ec2",
        confirm_recipient="",
        profile=settings.get("profile", ""),
        region=settings["region"],
        size_key=settings["size"],
        step_labels={},
        login_target=login_target,
        subnet_id="",
    )
    if refusal is not None or job is None:
        body = refusal.body if refusal is not None else {}
        raise sc.CardRejected(
            str(body.get("error") or "the home could not start building"),
            str(body.get("code") or "launch_refused"),
        )

    def _remember(c: sc.SetupCard) -> None:
        c.private["job_id"] = job.id

    from kiro_crew.dashboard.home_signin import BROWSER_HERE_KEY

    # Read off the card this click claimed, in memory: only the click's own
    # watcher may open a sign-in page in the owner's browser.
    may_open = card.private.get(BROWSER_HERE_KEY) is True
    await asyncio.to_thread(sc.update_card, card.id, _remember)
    card = await _finish(card, sc.STATUS_WAITING, outcome=_home_outcome(job))
    task = asyncio.create_task(_watch_home(state, card.id, job.id, may_open=may_open))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return card


async def _chosen_home_size(
    card: sc.SetupCard, input_: dict[str, Any], settings: dict[str, Any]
) -> str:
    """The size the Build click chose, checked against what the card offered.

    Refused: a size the card did not offer, a paid-plan size on the Free plan
    (whose EC2 launches free-tier types only), and a size above the account's EC2
    vCPU quota in the home's region (read-only, before anything is spent).
    """
    from kiro_crew.cloud import local_signin
    from kiro_crew.cloud.sizes import get_tier

    offered = [
        str(o.get("key"))
        for o in card.payload.get("size_options") or []
        if isinstance(o, dict) and o.get("key")
    ]
    if not offered:
        return str(settings.get("size") or sc.HOME_DEFAULT_SIZE)
    size = str(input_.get("size") or card.payload.get("size_default") or offered[0])
    if size not in offered:
        raise sc.CardRejected("choose one of the sizes on the card", "home_size_not_offered")
    tier = get_tier(size)
    raw_plan = card.payload.get("plan")
    plan: dict[str, Any] = raw_plan if isinstance(raw_plan, dict) else {}
    if plan.get("type") == local_signin.PLAN_FREE and not tier.free_plan_ok:
        raise sc.CardRejected(
            "this size needs the AWS paid plan, and this account is on the Free plan; "
            "choose Starter, or upgrade the account on AWS first",
            "home_size_needs_paid_plan",
        )
    if not card.payload.get("simulated"):
        quota = await asyncio.to_thread(
            local_signin.vcpu_quota, str(settings.get("profile") or ""), settings["region"]
        )
        if quota is not None and quota < tier.vcpu:
            raise sc.CardRejected(
                f"this AWS account may run {quota} vCPUs in {settings['region']}, and this "
                f"size needs {tier.vcpu}; raise the quota in Service Quotas, or choose a "
                "smaller size",
                "home_vcpu_quota_low",
            )
    return size


async def _inherited_login_target() -> Any:
    """The Kiro sign-in the home should use: the one this machine is signed in with.

    The same inheritance `kirocrew cloud launch` applies: an Identity Center
    sign-in yields that organization's start URL, so the home's sign-in page is
    the one the user already uses, not the Builder ID default. Unknown (whoami
    did not answer, or named Identity Center without a start URL) is ``None``,
    which leaves the launch engine's default.
    """
    from kiro_crew.cloud.login_target import target_from_whoami
    from kiro_crew.dashboard.handlers.sessions import fetch_local_identity

    try:
        identity = await fetch_local_identity()
    except Exception:
        logger.debug("local Kiro identity lookup for the home failed", exc_info=True)
        return None
    if identity is None:
        return None
    target = target_from_whoami(identity)
    return None if target is None or target.is_default else target


#: How AWS words a refusal from the account's spend limit, in a launch error.
_SPEND_LIMIT_RE = re.compile(r"spend(?:ing)?[\s_-]*limit", re.IGNORECASE)
#: Polls in a row the home card may fail to read its build before it stops it.
_UNTRACKED_POLLS = 3


def _home_outcome(job: Any, **extra: Any) -> dict[str, Any]:
    data = job.to_dict()
    out: dict[str, Any] = {
        "job_id": data.get("id"),
        "status": data.get("status"),
        "steps": data.get("steps", []),
        "error": data.get("error") or "",
    }
    signin = data.get("signin")
    if isinstance(signin, dict) and signin.get("url"):
        out["signin"] = _shown_signin(str(data.get("id") or ""), signin)
    out.update(extra)
    return out


def _shown_signin(job_id: str, from_file: dict[str, Any]) -> dict[str, str]:
    """The sign-in link and code the home card shows the owner.

    The copy this process's launch worker received from the home when there is
    one (``launch_job.issued_signin``): the job file is under ``run/``, which a
    sandboxed shell can write, so its copy could be a device code an agent
    planted for the owner to approve. The file's copy only after a restart,
    when no worker here holds one, as before.
    """
    from kiro_crew.cloud.launch_job import issued_signin

    issued = issued_signin(job_id) if job_id else None
    if issued is not None:
        prompt, _start_url = issued
        return {"url": prompt.url, "code": prompt.code}
    return {"url": str(from_file.get("url") or ""), "code": str(from_file.get("code") or "")}


async def _watch_home(
    state: "DashboardState", card_id: str, job_id: str, *, may_open: bool = False
) -> None:
    """Mirror the launch job onto the home card until it ends.

    While the build waits on the home's own Kiro sign-in, ``home_signin`` opens
    its page in the owner's browser (only when *may_open*) and posts the notice.
    """
    from kiro_crew.cloud import launch_job as lj
    from kiro_crew.dashboard.handlers_cloud import _store

    store = _store(state)
    last: dict[str, Any] | None = None
    misses = 0
    try:
        while True:
            await asyncio.sleep(_CONNECT_POLL_SECS)
            card = await asyncio.to_thread(sc.get_card, card_id)
            if card is None or card.status != sc.STATUS_WAITING:
                return
            try:
                job = await asyncio.to_thread(store.get, job_id)
            except Exception:
                logger.warning("home card %s: its build %s is unreadable", card_id, job_id)
                job = None
            if job is None:
                misses += 1
                if misses >= _UNTRACKED_POLLS:
                    await _stop_untracked_build(state, card_id, job_id)
                    return
                continue
            misses = 0
            outcome = _home_outcome(job)
            if job.status == lj.DONE:
                card = await _home_built(card, job, outcome)
                broadcast(state, card)
                return
            if job.terminal:
                message = str(job.error or "the home could not be built")
                code = (
                    "home_spend_limit" if _SPEND_LIMIT_RE.search(message) else "home_build_failed"
                )
                card = await _finish(card, sc.STATUS_FAILED, outcome=outcome, error=(code, message))
                broadcast(state, card)
                await _report(state, card)
                return
            if outcome != last:
                last = outcome
                card = await _finish(card, sc.STATUS_WAITING, outcome=outcome)
                broadcast(state, card)
            if outcome.get("signin"):
                from kiro_crew.dashboard.home_signin import prompt_signin

                await prompt_signin(
                    state, card, outcome["signin"], job_id=job_id, may_open=may_open
                )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("home card %s watcher failed", card_id)
        await _stop_untracked_build(state, card_id, job_id)


async def _stop_untracked_build(state: "DashboardState", card_id: str, job_id: str) -> None:
    """The card cannot follow its build: stop the build, and say so.

    A build the owner cannot see is a build nobody would stop, and it creates
    billed AWS resources. So the build is cancelled the way the launch's own
    cancel does it (the in-process worker's event; the worker rolls its stack
    back at its next checkpoint), and the card fails with a message instead of
    waiting forever. Never raises.
    """
    try:
        from kiro_crew.dashboard.handlers_cloud import _cancels

        event = _cancels(state).get(job_id)
        if event is not None:
            event.set()
        card = await asyncio.to_thread(sc.get_card, card_id)
        if card is None or card.status != sc.STATUS_WAITING:
            return
        card = await _finish(
            card,
            sc.STATUS_FAILED,
            outcome={"job_id": job_id, "stopped": event is not None},
            error=(
                "home_build_untracked",
                "the home's build could not be followed any more, so it was stopped; "
                "whatever it had created in AWS is being removed",
            ),
        )
        broadcast(state, card)
        await _report(state, card)
    except Exception:
        logger.exception("home card %s: stopping its untracked build failed", card_id)


async def _home_built(card: sc.SetupCard, job: Any, outcome: dict[str, Any]) -> sc.SetupCard:
    """A finished build: offer Move in, or first the home's own Kiro sign-in.

    A build can finish with its sign-in step skipped: the device code ran out
    unapproved, or a gateway restart cut the wait short. That home's agent cannot
    answer, so moving the crew there would strand it; the card stays in phase
    ``signin`` until the home is signed in.
    """
    signed_in = bool(job.signin_detected) or bool(card.payload.get("simulated"))
    phase = "move" if signed_in else "signin"

    def _mark(c: sc.SetupCard) -> None:
        c.private["phase"] = phase
        c.private["instance_id"] = job.instance_id

    await asyncio.to_thread(sc.update_card, card.id, _mark)
    extra = {"ready": True} if signed_in else {"ready": False, "needs_signin": True}
    return await _finish(card, sc.STATUS_PENDING, outcome={**outcome, **extra})


async def _sign_home_in(state: "DashboardState", card: sc.SetupCard) -> sc.SetupCard:
    """Start the built home's Kiro sign-in again, then watch it like the build.

    The same restart the Instances hub's "Start sign-in" runs
    (``handlers_cloud.restart_signin``), with every refusal it has. The page may
    open in the owner's browser when this click came from this machine (decide
    has just recorded that on the card).
    """
    from kiro_crew.dashboard.handlers_cloud import _store, restart_signin
    from kiro_crew.dashboard.home_signin import BROWSER_HERE_KEY

    job_id = str(card.private.get("job_id") or "")
    if not job_id:
        raise sc.CardRejected(
            "this home's build is not known here, so it cannot be signed in from this card",
            "home_signin_unavailable",
        )
    job, refusal = await restart_signin(state, job_id)
    if refusal is not None or job is None:
        body = refusal.body if refusal is not None else {}
        code = str(body.get("code") or "launch_refused")
        if code == "signin_already_complete":
            # Signed in meanwhile (another tab, the Instances hub): move in.
            done = await asyncio.to_thread(_store(state).get, job_id)
            if done is not None:
                return await _home_built(card, done, _home_outcome(done))
        raise sc.CardRejected(str(body.get("error") or "the sign-in could not start"), code)
    may_open = card.private.get(BROWSER_HERE_KEY) is True
    card = await _finish(card, sc.STATUS_WAITING, outcome=_home_outcome(job))
    task = asyncio.create_task(_watch_home(state, card.id, job.id, may_open=may_open))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return card


#: The move-in steps a home card shows, in order.
MOVE_IN_STEPS: tuple[tuple[str, str], ...] = (
    ("pack", "Pack memory, schedules, settings and persona"),
    ("carry", "Carry them to your home"),
    ("unpack", "Unpack them there"),
    ("chat", "Bring this chat along"),
)


async def _move_in(state: "DashboardState", card: sc.SetupCard) -> sc.SetupCard:
    """Hand the crew to its home. Simulated homes walk the steps without moving data.

    A live home is handed over by ``dashboard/setup_move_in.py``.
    """
    steps = [
        {"key": k, "label": label, "state": "pending", "detail": ""} for k, label in MOVE_IN_STEPS
    ]
    base = dict(card.outcome or {})
    if card.payload.get("simulated"):
        for step in steps:
            step["state"] = "active"
            card = await _finish(card, sc.STATUS_WORKING, outcome={**base, "move_steps": steps})
            broadcast(state, card)
            await asyncio.sleep(1.5)
            step["state"] = "done"
        await asyncio.to_thread(mark_stage, "stay_on")
        return await _finish(
            card,
            sc.STATUS_COMMITTED,
            outcome={**base, "move_steps": steps, "moved": True, "simulated": True},
        )
    from kiro_crew.dashboard.setup_move_in import move_in

    return await move_in(state, card)


async def _commit_channel(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    """Store the bot token and wait for ``/pair`` (``dashboard/setup_channel.py``)."""
    from kiro_crew.dashboard.setup_channel import commit_channel

    return await commit_channel(state, card, input_)


_Committer = Callable[["DashboardState", sc.SetupCard, dict[str, Any]], Awaitable[sc.SetupCard]]

_COMMITTERS: dict[str, _Committer] = {
    sc.KIND_PRIVACY: _commit_privacy,
    sc.KIND_PROFILE: _commit_profile,
    sc.KIND_SOUL: _commit_soul,
    sc.KIND_IMPORT: _commit_import,
    sc.KIND_CONNECT: _commit_connect,
    sc.KIND_CREDENTIAL: _commit_credential,
    sc.KIND_CHANNEL: _commit_channel,
    sc.KIND_CRON: _commit_cron,
    sc.KIND_SERVICE: _commit_service,
    sc.KIND_HOME: _commit_home,
}
_PREVIEWERS: dict[str, _Committer] = {sc.KIND_CRON: _preview_cron}


# ── first run ───────────────────────────────────────────────────────────────


def _has_any_session(state: "DashboardState") -> bool:
    log = getattr(state, "conversation_log", None)
    if log is None:
        return False
    try:
        return bool(log.list_sessions())
    except Exception:
        logger.warning("session listing failed; assuming sessions exist", exc_info=True)
        return True


async def ensure_first_run_session(state: "DashboardState") -> str | None:
    """Create the first-run chat on a fresh install; return its slot key.

    Idempotent across restarts: an existing first-run slot is reused, and an
    install that is onboarded or already has sessions never gets one.
    """
    from kiro_crew.config.loader import KiroCrewConfig
    from kiro_crew.dashboard import setup_guardrails
    from kiro_crew.dashboard.state import SlotOrigin

    existing = await asyncio.to_thread(read_first_run_slot)
    if existing:
        if state.get_slot(existing) is None:
            return None
        setup_guardrails.track(state, existing)
        return existing
    cfg = await asyncio.to_thread(KiroCrewConfig.load)
    if cfg.dashboard.onboarded or cfg.dashboard.privacy_acked:
        return None
    if state.live_slot_count() > 0 or await asyncio.to_thread(_has_any_session, state):
        return None
    from kiro_crew.agent_files import MAIN_CHAT_AGENT_NAME

    with state.suspend_slots_push():
        # The first-run chat becomes the main chat, which hands long work to its
        # own chats: it runs on the default agent plus the session tools. Set at
        # creation, because switching a chat's agent later resets its session.
        slot = state.get_or_create_slot(None, agent=MAIN_CHAT_AGENT_NAME, origin=SlotOrigin.SYSTEM)
        slot.pinned = True
    # An explicit title, so the first message does not retitle the first-run chat
    # before graduation names it after the agent.
    await _set_explicit_title(state, slot, FIRST_RUN_TITLE)
    await asyncio.to_thread(record_slot, slot.key)
    session_key = f"dashboard:{slot.key}"
    card = await asyncio.to_thread(
        lambda: sc.create_card(
            slot=slot.key, session_key=session_key, kind=sc.KIND_PRIVACY, payload={}
        )
    )
    _append_card_row(state, slot, card)
    state.push_slots_update()
    setup_guardrails.track(state, slot.key)
    logger.info("first-run session created: %s", slot.key)
    return slot.key


def _kickoff_facts(slot_key: str = "") -> list[str]:
    facts: list[str] = []
    try:
        plan = _import_plan()
        for source in plan.get("sources", []):
            counts = {
                c.get("id"): c.get("count", 0)
                for c in source.get("categories", [])
                if isinstance(c, dict) and c.get("count")
            }
            if counts:
                listed = ", ".join(f"{n} {cat}" for cat, n in counts.items())
                facts.append(f"Another agent is installed here: {source.get('name')} ({listed}).")
    except Exception:
        logger.warning("import scan for the first-run kickoff failed", exc_info=True)
    try:
        from kiro_crew.connections.registry import get_visible_providers

        names = [str(p.get("name", p["slug"])) for p in get_visible_providers()][:12]
        facts.append("Curated connections available: " + ", ".join(names) + ".")
    except Exception:
        logger.warning("provider listing for the first-run kickoff failed", exc_info=True)
    try:
        from kiro_crew.first_run import read_state

        state = read_state()
        home = state.get("home")
        card = _home_card_in(slot_key) if slot_key else None
        if card is not None and card.payload.get(HOME_STEP_KEY):
            facts.append(_home_step_fact(card))
        elif isinstance(home, dict) and home.get("choice") == "cloud":
            facts.append(
                "The user chose a home in the cloud (their AWS account) in the terminal; a "
                "home card is shown at the top of this chat. Do not wait for it — carry on "
                "with the setup; offer Move in when the card says the home is ready."
            )
    except Exception:
        logger.warning("home choice read for the first-run kickoff failed", exc_info=True)
    try:
        facts.append(
            "Keep-running service installed: "
            + ("yes." if _service_payload()["installed"] else "no.")
        )
    except Exception:
        logger.warning("service probe for the first-run kickoff failed", exc_info=True)
    return facts


def _home_card_in(slot_key: str) -> "sc.SetupCard | None":
    for card in sc.list_cards(slot_key):
        if card.kind == sc.KIND_HOME:
            return card
    return None


def _home_step_fact(card: "sc.SetupCard") -> str:
    """What the Hello is told about the home step the gateway put on screen."""
    p = card.payload
    raw_options = p.get("size_options")
    options = (
        [o for o in raw_options if isinstance(o, dict)] if isinstance(raw_options, list) else []
    )
    prices = [o["monthly_usd"] for o in options if isinstance(o.get("monthly_usd"), (int, float))]
    if prices:
        # The card offers sizes; the Hello quotes the cheapest, not one the user
        # may never pick.
        cost = (
            f"sizes from about ${min(prices)}/month, chosen on the card, billed by AWS"
            if len(prices) > 1
            else f"about ${prices[0]}/month, billed by AWS"
        )
    else:
        raw_size = p.get("size")
        size: dict[str, Any] = raw_size if isinstance(raw_size, dict) else {}
        cost = (
            f"about ${p.get('monthly_usd')}/month on {size.get('instance_type', '')}, "
            "billed by AWS"
        )
    where = (
        f"this machine's AWS CLI is signed in (account {p.get('aws_account') or 'unknown'}, "
        f"region {p.get('region')})"
        if p.get("aws_signed_in")
        else "this machine is not signed in to AWS, so the card first walks the user "
        "through it: installing the AWS CLI if it is missing, creating an AWS account "
        "if they have none, and signing in"
    )
    return (
        'Right after privacy the gateway showed a "Where should your crew live?" card: '
        f"stay on this machine, or a home in the cloud ({cost}) that keeps jobs running "
        f"when this machine sleeps; {where}. It is the user's step, on screen. In the "
        "Hello, point to it in one sentence; do not ask it again in prose and do not "
        "wait for it. If they pick the cloud and AWS is not set up, guide them through "
        "the card's steps in the chat and answer questions; once they press Build it "
        "runs in the background while setup carries on. If they keep it on this "
        "machine, do not bring it up again during setup."
    )


async def start_first_run_turn(state: "DashboardState", slot: "_ChatSlot") -> None:
    """Dispatch the first model turn of the first-run session."""
    from kiro_crew.dashboard import setup_guardrails
    from kiro_crew.dashboard.state import FIRST_RUN_END, FIRST_RUN_PREFIX

    facts = await asyncio.to_thread(_kickoff_facts, slot.key)
    lines = "\n".join(f"- {fact}" for fact in facts) or "- Nothing else was detected."
    text = (
        f"{FIRST_RUN_PREFIX} This is a brand-new Kiro Crew install and this chat is its "
        "first-run session. The user just acknowledged the privacy disclosure. $crew-setup\n"
        "Facts the gateway gathered (not from the user):\n"
        f"{lines}\n"
        "Follow the crew-setup skill. Open with what you found, keep it short, and use "
        "setup_card for every change.\n"
        f"{FIRST_RUN_END}"
    )
    setup_guardrails.expect_kickoff(state, slot.key)
    try:
        await _dispatch_envelope_turn(state, slot, text, "first_run")
    except Exception:
        logger.warning("first-run kickoff could not be dispatched", exc_info=True)
        setup_guardrails.kickoff_failed(state, slot)


# ── the main chat ───────────────────────────────────────────────────────────

#: Most other chats the overview names; the rest are counted.
_OVERVIEW_MAX_CHATS = 8
#: Most jobs the overview names.
_OVERVIEW_MAX_JOBS = 5
#: Hard cap on the overview block, so it never crowds the turn's context.
OVERVIEW_MAX_CHARS = 1800


def _agent_name() -> str:
    from kiro_crew.config.loader import KiroCrewConfig

    cfg = KiroCrewConfig.load()
    name = str(getattr(cfg.agent, "bot_name", "") or getattr(cfg.dashboard, "bot_name", "") or "")
    return name.strip()


#: The first-run chat's title until graduation renames it.
FIRST_RUN_TITLE = "Welcome to Kiro Crew"

#: The main chat's title while the agent has no name yet.
MAIN_CHAT_FALLBACK_TITLE = "Main"


async def graduate(state: "DashboardState", slot_key: str) -> bool:
    """Turn the first-run chat into the main chat once its first job is kept.

    Idempotent. Renames the chat after the agent (as an explicit title, so the
    auto-titler leaves it alone), keeps it pinned, records it as the main chat and
    posts a deterministic notice. Returns whether it graduated now.
    """
    from kiro_crew.dashboard.system_notices import MAIN_CHAT_KIND
    from kiro_crew.first_run import is_first_run_slot, read_main_slot, record_main

    if not await asyncio.to_thread(is_first_run_slot, slot_key):
        return False
    if await asyncio.to_thread(read_main_slot):
        return False
    slot = state.get_slot(slot_key)
    if slot is None:
        return False
    name = await asyncio.to_thread(_agent_name)
    await _set_explicit_title(state, slot, name or MAIN_CHAT_FALLBACK_TITLE)
    slot.pinned = True
    await asyncio.to_thread(record_main, slot_key)
    await asyncio.to_thread(mark_stage, "main")
    slot.append(
        "assistant",
        f"Setup is done. This is your main chat{f' with {name}' if name else ''}: start "
        "here, and ask for anything else — other chats, schedules, connections, your "
        "home — from here.",
        "msg msg-system",
        meta={"kind": MAIN_CHAT_KIND},
    )
    state.push_slots_update()
    logger.info("first-run chat %s graduated to the main chat", slot_key)
    return True


async def make_main_chat(state: "DashboardState", slot_key: str) -> str:
    """Make one of the owner's own dashboard chats the main chat, on request.

    For an install that never had a first run, or a user who wants a different
    chat to be the one the product opens. The previous main chat simply loses
    the marker; nothing else about either chat changes.
    """
    from kiro_crew.dashboard.system_notices import MAIN_CHAT_KIND
    from kiro_crew.first_run import record_main

    slot = state.get_slot(slot_key)
    if slot is None:
        raise sc.CardRejected("that chat is not open", "slot_not_found")
    if (
        not slot_key.startswith("chat-")
        or getattr(slot, "channel_origin", None)
        or getattr(slot, "_slack_linked", False)
    ):
        raise sc.CardRejected(
            "only one of your own dashboard chats can be the main chat", "slot_not_eligible"
        )
    await asyncio.to_thread(record_main, slot_key)
    slot.pinned = True
    slot.append(
        "assistant",
        "This is now your main chat: the product opens here, and you can ask for "
        "anything else — other chats, schedules, connections, your home — from here.",
        "msg msg-system",
        meta={"kind": MAIN_CHAT_KIND},
    )
    state.push_slots_update()
    return slot_key


def _plain(text: Any, limit: int = 60) -> str:
    """A title safe to quote inside a context block: no brackets, one line, bounded."""
    out = " ".join(str(text or "").split()).replace("[", "(").replace("]", ")")
    return out if len(out) <= limit else out[: limit - 1] + "…"


def _slot_status(slot: Any) -> str:
    if getattr(slot, "_question_pending", None):
        return "waiting on you"
    if getattr(slot, "running", False):
        return "working"
    return "idle"


async def crew_overview(state: "DashboardState", slot: "_ChatSlot") -> str:
    """The ``[CREW OVERVIEW]`` block for the main chat's next turn, or ``""``.

    Information about the user's own sessions, jobs and cards -- what
    ``list_sessions`` and ``setup_status`` already answer -- so it widens
    nothing; it saves the main chat a lookup. Bounded by
    :data:`OVERVIEW_MAX_CHARS`.
    """
    from kiro_crew.first_run import read_main_slot

    if await asyncio.to_thread(read_main_slot) != slot.key:
        return ""
    lines: list[str] = []
    others = [
        s
        for s in list(getattr(state, "_slots", {}).values())
        if getattr(s, "key", "") != slot.key and not str(getattr(s, "key", "")).startswith("cron-")
    ]
    active = [s for s in others if _slot_status(s) != "idle"]
    if others:
        named = (active + [s for s in others if s not in active])[:_OVERVIEW_MAX_CHATS]
        chats = "; ".join(
            f"\"{_plain(getattr(s, 'title', s.key))}\" — {_slot_status(s)}" for s in named
        )
        more = len(others) - len(named)
        lines.append(f"- Other chats: {chats}" + (f"; and {more} more" if more > 0 else "") + ".")
    cards = await asyncio.to_thread(sc.load_cards)
    open_cards = [c for c in cards if not c.terminal]
    if open_cards:
        items = "; ".join(
            f"{_plain(_card_title(c))} ({c.status}{'' if c.slot == slot.key else ', another chat'})"
            for c in open_cards[-5:]
        )
        lines.append(f"- Setup cards open: {items}.")
    crons = getattr(state, "crons", None)
    if crons is not None:
        try:
            jobs = [j for j in await crons.list_jobs_async() if getattr(j, "enabled", False)]
        except Exception:
            jobs = []
        if jobs:
            from kiro_crew.cron import compute_next_run_ts

            def _next(job: Any) -> float:
                try:
                    return compute_next_run_ts(job) or float("inf")
                except Exception:
                    return float("inf")

            jobs.sort(key=_next)
            names = ", ".join(
                f"\"{_plain(getattr(j, 'name', ''))}\"" for j in jobs[:_OVERVIEW_MAX_JOBS]
            )
            lines.append(f"- Scheduled jobs ({len(jobs)}), next due first: {names}.")
    home = next((c for c in reversed(cards) if c.kind == sc.KIND_HOME), None)
    if home is not None:
        state_word = (
            "moved in"
            if (home.outcome or {}).get("moved")
            else "ready to move in" if (home.outcome or {}).get("ready") else home.status
        )
        lines.append(f"- Home in the cloud: {state_word}.")
    from kiro_crew.context import _neutralize_structural_markers

    # Chat and job titles are user- and agent-authored; a forged block marker in
    # one must not close or open a block in the assembled prompt.
    body = (
        _neutralize_structural_markers("\n".join(lines)) if lines else "- Nothing else is running."
    )
    block = (
        "[CREW OVERVIEW]\n"
        "You are in the user's main chat. What else is happening (gathered by the gateway):\n"
        f"{body}\n"
        "Delegate long work to its own chat (session_create, session_send) or a sub-agent. "
        "A note appears here when that chat finishes; read it with session_read_message "
        "when the user asks.\n"
        "[End of crew overview]\n\n"
    )
    if len(block) > OVERVIEW_MAX_CHARS:
        block = block[: OVERVIEW_MAX_CHARS - 40] + "…\n[End of crew overview]\n\n"
    return block


def first_run_slot_for_theme() -> str | None:
    """The first-run slot to report to the SPA (presentation only)."""
    return read_first_run_slot()
