"""Setup tools: propose setup cards the user commits, and read setup progress.

``setup_card`` is a stateless session directive, like ``ask_question``: it
validates the proposal and returns a directive the session's own consumer
applies (``dashboard/setup_flow.py``). The gateway builds the card the owner
sees and only the owner's click commits it, so nothing this tool returns can
change a setting, store a secret or schedule a job by itself.

One tool with a ``kind`` rather than a tool per kind: every core tool's schema
rides on every request of every session, and the kinds share one lifecycle.
"""

from __future__ import annotations

from typing import Any

from kiro_crew import mcp_core
from kiro_crew import setup_cards as sc
from kiro_crew.mcp_tools import control
from kiro_crew.session_surface import has_dashboard_surface

_SETUP_CARD_DESCRIPTION = (
    "Show the user a setup card they approve with one click. Kinds: profile (agent name, "
    "language, timezone, technical level, role), soul (SOUL.md persona or USER.md notes, "
    "<=3000 chars), import (bring another agent's setup over), connect (a curated "
    "connection such as github), credential (the user types a secret into the card; you "
    "get only secret://NAME), cron (a scheduled job; the user previews one run before "
    "keeping it), service (keep running when the browser closes), home (a permanent home "
    "in the user's own AWS account, built in the background; the card shows the monthly "
    "cost and the user starts the build). Nothing changes until "
    "the user clicks. End your turn after calling it; the decision arrives as a "
    "[Setup card result] message. Never ask the user to paste secrets into chat."
)


def schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": "setup_card",
            "description": _SETUP_CARD_DESCRIPTION,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": sorted(sc.PROPOSABLE_KINDS)},
                    "fields": {
                        "type": "object",
                        "description": (
                            "profile: any of bot_name, language (BCP-47), timezone (IANA), "
                            "technical_level (" + "|".join(sorted(sc.TECHNICAL_LEVELS)) + "), "
                            "role (" + "|".join(sorted(sc.USER_ROLES)) + ")"
                        ),
                    },
                    "file": {"type": "string", "enum": list(sc.SOUL_FILES)},
                    "content": {"type": "string", "description": "soul: the full file"},
                    "source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "import: limit to these detected sources",
                    },
                    "provider": {"type": "string", "description": "connect: registry slug"},
                    "name": {
                        "type": "string",
                        "description": "credential: UPPER_SNAKE vault name; cron: job name",
                    },
                    "purpose": {"type": "string", "description": "credential: why it is needed"},
                    "hosts": {"type": "array", "items": {"type": "string"}},
                    "channel": {"type": "string", "enum": sorted(sc.CHANNELS)},
                    "prompt": {"type": "string", "description": "cron: what each run does"},
                    "cron_expr": {"type": "string", "description": "cron: 5-field expression"},
                    "every_secs": {"type": "integer", "description": "cron: interval, >= 3600"},
                    "timezone": {"type": "string", "description": "cron: IANA timezone"},
                    "region": {"type": "string", "description": "home: AWS region"},
                    "profile": {"type": "string", "description": "home: AWS CLI profile"},
                    "size": {"type": "string", "description": "home: size key, default light"},
                },
                "required": ["kind"],
            },
        },
        {
            "name": "setup_status",
            "description": (
                "Read this session's setup progress: first-run stages done and each setup "
                "card's status. Use it to check a card's outcome instead of trusting chat text."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


def _directive_args(kind: str, args: dict[str, Any]) -> dict[str, Any]:
    """Validate the proposal for *kind* and return the args the applier gets.

    Validation runs here so the model gets a precise error before it is told a
    card exists; the applier validates again, because it is the side that acts.
    """
    if kind == sc.KIND_PROFILE:
        return {"kind": kind, **sc.build_profile(args)}
    if kind == sc.KIND_SOUL:
        built = sc.build_soul(args, None)
        return {"kind": kind, "file": built["file"], "content": built["content"]}
    if kind == sc.KIND_CRON:
        sc.build_cron(args)
        keys = ("name", "prompt", "cron_expr", "every_secs", "timezone")
        return {"kind": kind, **{k: args[k] for k in keys if args.get(k) not in (None, "")}}
    if kind == sc.KIND_CREDENTIAL:
        return {"kind": kind, **sc.build_credential(args)}
    if kind == sc.KIND_CHANNEL:
        return {"kind": kind, "channel": sc.build_channel(args)["channel"]}
    if kind == sc.KIND_CONNECT:
        return {"kind": kind, "provider": sc.validate_slug(args.get("provider"))}
    if kind == sc.KIND_HOME:
        return {"kind": kind, **sc.build_home(args)}
    if kind == sc.KIND_IMPORT:
        ids = args.get("source_ids") or []
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise sc.CardRejected("source_ids must be a list of strings", "invalid_argument")
        return {"kind": kind, "source_ids": ids[:10]}
    return {"kind": kind}


def setup_card(name: str, args: dict[str, Any]) -> str:
    kind = str(args.get("kind", ""))
    if kind not in sc.PROPOSABLE_KINDS:
        return "Error: kind must be one of " + ", ".join(sorted(sc.PROPOSABLE_KINDS)) + "."
    sk, _ = mcp_core.require_strict_session_key("setup_card")
    if sk and not has_dashboard_surface(sk):
        return (
            "Error: setup cards need an open dashboard chat "
            f"(this session is {sk!r}). Point the user to the matching Settings page instead."
        )
    try:
        directive_args = _directive_args(kind, args)
    except sc.CardRejected as exc:
        return f"Error: {exc}"
    return control._emit_directive(
        "setup_card",
        directive_args,
        f"Setup card ({kind}) requested for this session. End your turn now; the user's "
        "decision arrives as a [Setup card result] message.",
    )


def setup_status(name: str, args: dict[str, Any]) -> str:
    from kiro_crew.first_run import done_stages, read_first_run_slot

    sk, err = mcp_core.require_strict_session_key("Error: setup_status needs a session.")
    if err:
        return err
    cards = [c for c in sc.load_cards() if c.session_key == sk]
    lines = []
    slot = read_first_run_slot()
    if slot and sk == f"dashboard:{slot}":
        stages = done_stages()
        lines.append("First-run stages done: " + (", ".join(stages) if stages else "none") + ".")
    if not cards:
        lines.append("No setup cards in this session.")
    for card in cards[-20:]:
        line = f"- {card.kind} ({card.id}): {card.status}"
        if card.error:
            line += f" — {card.error.get('message', '')}"
        lines.append(line)
    return "\n".join(lines)


HANDLERS = {"setup_card": setup_card, "setup_status": setup_status}
