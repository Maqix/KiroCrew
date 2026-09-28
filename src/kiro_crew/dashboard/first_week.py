"""The first week after the first run: one short tip a day in the main chat.

RFC one-chat first run §5.8. The first run stops at the first kept job, so what
it did not cover (a connection, staying on, a channel, a second job, SOUL.md, a
skill) is offered here, one thing at a time, over the next seven days, instead
of making the first run longer.

A tip is a deterministic system notice the gateway writes itself. It runs no
model turn, so it costs no harness quota, and it never raises a setup card:
cards need a turn a person started (SC8), so the user's reply is what brings
one. Tips post only on a day the user has used the main chat, stop after two in
a row get no reply, stop when the user says so, and end after seven days. The
bookkeeping lives in the first-run state file and, like the rest of that file,
decides only what is shown.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from kiro_crew import setup_cards as sc
from kiro_crew.first_run import read_main_slot, read_state, update_state

if TYPE_CHECKING:
    from kiro_crew.dashboard.state import DashboardState

logger = logging.getLogger(__name__)

#: How long after graduation tips run, and how often at most.
TIP_DAYS = 7
TIP_MIN_GAP_SECS = 20 * 3600
#: Tips post only in the user's daytime, local clock.
TIP_EARLIEST_HOUR = 9
TIP_LATEST_HOUR = 20
#: "Used the product today": a message from the user in the main chat this recently.
ACTIVE_WITHIN_SECS = 36 * 3600
#: Two tips in a row with no reply end the week early.
MAX_UNANSWERED = 2
#: How often the gateway checks whether a tip is due.
CHECK_EVERY_SECS = 30 * 60

_STATE_KEY = "first_week"
_STOP_RE = re.compile(r"\b(?:no more|stop(?: the)?|enough)\s+tips\b", re.IGNORECASE)


@dataclass(frozen=True)
class Tip:
    id: str
    text: str
    applies: Callable[[dict[str, Any]], bool]


TIPS: tuple[Tip, ...] = (
    Tip(
        "connect",
        "Tip: connect GitHub (or Linear, GitLab, Sentry) and your morning brief can "
        'include the reviews waiting on you. Say "connect GitHub" when you want it.',
        lambda f: not f["connected"],
    ),
    Tip(
        "stay_on",
        'Tip: jobs pause while this machine sleeps. Say "keep running" and I\'ll stay '
        'on — or "move me to the cloud" for a home that never sleeps.',
        lambda f: not f["service"] and not f["moved"],
    ),
    Tip(
        "channel",
        'Tip: want your brief on your phone? Say "connect Telegram" and you can chat '
        "with me there too.",
        lambda f: not f["channel"],
    ),
    Tip(
        "watch",
        'Tip: I can watch a pull request and tell you when it needs you. Say "watch '
        '<link to the PR>".',
        lambda f: f["jobs"] < 2,
    ),
    Tip(
        "soul",
        "Tip: tell me how you like answers (shorter, no emoji, a language) and I'll "
        "keep it in SOUL.md, where you can read and edit it.",
        lambda f: not f["soul"],
    ),
    Tip(
        "skill",
        'Tip: when you find yourself asking me the same thing twice, say "make that a '
        "skill\" and I'll write it down so it runs the same way every time.",
        lambda f: True,
    ),
)


def _week_state() -> dict[str, Any]:
    raw = read_state().get(_STATE_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _save_week_state(week: dict[str, Any]) -> None:
    update_state(lambda state: state.__setitem__(_STATE_KEY, week))


def graduated_at() -> float | None:
    """When the first job was kept (the main chat's graduation), if it was."""
    stages = read_state().get("stages")
    if not isinstance(stages, dict):
        return None
    ts = stages.get("job_kept")
    return float(ts) if isinstance(ts, (int, float)) else None


def stop_tips() -> None:
    """End the first-week tips for good."""
    week = _week_state()
    week["stopped"] = True
    _save_week_state(week)


def note_user_message(slot_key: str, text: str) -> bool:
    """Stop the tips when the user asks in the main chat. Returns whether it stopped them."""
    if not text or slot_key != read_main_slot() or not _STOP_RE.search(text):
        return False
    stop_tips()
    return True


def _last_user_ts(slot: Any) -> float | None:
    for msg in reversed(getattr(slot, "messages", None) or []):
        if msg.get("role") != "user":
            continue
        raw = msg.get("ts")
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return None
    return None


def _facts(state: "DashboardState", main: str) -> dict[str, Any]:
    cards = sc.list_cards(main)
    committed = {c.kind for c in cards if c.status == sc.STATUS_COMMITTED}
    moved = any(c.kind == sc.KIND_HOME and (c.outcome or {}).get("moved") for c in cards)
    try:
        from kiro_crew.dashboard.setup_flow import _service_payload

        service = bool(_service_payload().get("installed"))
    except Exception:
        service = False
    try:
        jobs = sum(1 for j in state.crons.list_jobs() if getattr(j, "enabled", False))
    except Exception:
        jobs = 0
    return {
        "connected": sc.KIND_CONNECT in committed,
        "channel": sc.KIND_CHANNEL in committed,
        "service": service,
        "moved": moved,
        "jobs": jobs,
        "soul": bool(sc.read_persona("SOUL")),
    }


def next_tip(
    *,
    now: float,
    graduated: float | None,
    week: dict[str, Any],
    last_user: float | None,
    facts: dict[str, Any],
) -> Tip | None:
    """The tip to post now, or ``None``. Pure: every input is passed in."""
    if graduated is None or week.get("stopped"):
        return None
    days = (now - graduated) / 86400
    if days < 1 or days > TIP_DAYS:
        return None
    last_tip = week.get("last_ts")
    if isinstance(last_tip, (int, float)) and now - last_tip < TIP_MIN_GAP_SECS:
        return None
    hour = time.localtime(now).tm_hour
    if not TIP_EARLIEST_HOUR <= hour < TIP_LATEST_HOUR:
        return None
    if last_user is None or now - last_user > ACTIVE_WITHIN_SECS:
        return None
    shown = set(week.get("shown") or [])
    for tip in TIPS:
        if tip.id not in shown and tip.applies(facts):
            return tip
    return None


async def tick(state: "DashboardState", *, now: float | None = None) -> str | None:
    """Post today's tip in the main chat if one is due. Returns the tip id posted."""
    from kiro_crew.dashboard.system_notices import FIRST_WEEK_TIP_KIND

    now = time.time() if now is None else now
    main = await asyncio.to_thread(read_main_slot)
    slot = state.get_slot(main) if main else None
    if main is None or slot is None:
        return None
    week = await asyncio.to_thread(_week_state)
    graduated = await asyncio.to_thread(graduated_at)
    last_user = _last_user_ts(slot)
    last_tip = week.get("last_ts")
    if isinstance(last_tip, (int, float)) and (last_user is None or last_user < last_tip):
        unanswered = int(week.get("unanswered", 0)) + 1
    else:
        unanswered = 0
    facts = await asyncio.to_thread(_facts, state, main)
    tip = next_tip(now=now, graduated=graduated, week=week, last_user=last_user, facts=facts)
    if tip is None:
        return None
    if unanswered >= MAX_UNANSWERED:
        week["stopped"] = True
        await asyncio.to_thread(_save_week_state, week)
        return None
    slot.append(
        "assistant",
        tip.text + ' (Say "no more tips" to stop these.)',
        "msg msg-system",
        meta={"kind": FIRST_WEEK_TIP_KIND, "tip": tip.id},
    )
    week["shown"] = [*(week.get("shown") or []), tip.id]
    week["last_ts"] = now
    week["unanswered"] = unanswered
    await asyncio.to_thread(_save_week_state, week)
    state.push_slots_update()
    return tip.id


async def run(state: "DashboardState") -> None:
    """The gateway's tip loop: check every half hour until the week is over."""
    if not await asyncio.to_thread(lambda: bool(read_state().get("slot"))):
        return  # an install that never had a first run gets no tips
    while True:
        try:
            await tick(state)
            graduated = await asyncio.to_thread(graduated_at)
            week = await asyncio.to_thread(_week_state)
            if week.get("stopped") or (
                graduated is not None and time.time() - graduated > (TIP_DAYS + 1) * 86400
            ):
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("first-week tip check failed", exc_info=True)
        await asyncio.sleep(CHECK_EVERY_SECS)
