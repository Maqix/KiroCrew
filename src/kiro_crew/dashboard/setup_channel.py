"""The channel setup card: a bot token typed into the card, then ``/pair``.

RFC one-chat first run §5.3: a channel is connected "through a credential card
for the bot token, plus a ``/pair 4821`` message that allowlists the user's own
ID without asking them to look it up." Telegram is the one channel wired.

Committing a ``channel`` card does two things:

1. The token the owner typed into the card is stored where the Telegram channel
   reads it, ``TELEGRAM_BOT_TOKEN`` in the credential file, through the helper
   the Settings save uses (``handlers/messaging.commit_telegram_writes``), and
   the channel is turned on and reconnected. The token never enters the card,
   the store, the transcript, an event or a log (SC2).
2. The card goes ``waiting`` with a one-time pairing code. A ``/pair <code>`` DM
   to the bot adds THAT sender's numeric id to ``telegram.allowed_user_ids`` (the
   list the Settings page edits) and commits the card with
   ``{paired, username}``. A wrong code costs one of :data:`PAIR_MAX_WRONG`
   attempts; the last one fails the card. Unpaired after :data:`PAIR_WAIT_SECS`,
   the card expires.

While it is live the code is a bearer credential for the allow-list, so it is
held in this process's memory only and checked against memory only. The card
store is readable and writable from the agent's sandbox, and the model reads
cards through ``setup_status``: a code kept on disk could be read and relayed by
a steered agent, and a code checked against disk could be planted by one. The
owner's browser gets the code from the decide response, the owner-only card
event and the owner-only card routes (:func:`owner_view`). A gateway restart
drops a live code; the card then shows none, and the agent can propose a new one.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from kiro_crew import setup_cards as sc
from kiro_crew.dashboard import setup_flow as sf
from kiro_crew.first_run import mark_stage
from kiro_crew.sel import sel

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState

logger = logging.getLogger(__name__)

TELEGRAM = "telegram"
#: Digits in a pairing code: short enough to read off the card and type.
PAIR_CODE_DIGITS = 4
#: How long a pairing code stays live after the owner commits the card.
PAIR_WAIT_SECS = 600
#: Wrong codes the bot accepts, from anyone, before the pairing closes. With
#: :data:`PAIR_CODE_DIGITS` digits a guesser who found the bot has a
#: ``PAIR_MAX_WRONG / 10**PAIR_CODE_DIGITS`` chance in total.
PAIR_MAX_WRONG = 5

_REPLY_PAIRED = "Paired. Messages you send here now reach your agent."
_REPLY_WRONG = "That pairing code is not right. Check the code on the setup card and try again."
_REPLY_CLOSED = "Too many wrong codes, so pairing is closed. Start again from the dashboard."
_REPLY_FAILED = "Pairing could not be saved on the gateway. Start again from the dashboard."


@dataclass
class _Pairing:
    card_id: str
    code: str
    #: ``time.monotonic()`` past which the code does not pair.
    deadline: float
    wrong_left: int
    state: "DashboardState"


#: The live pairing per channel, at most one: committing a second channel card
#: replaces the first card's code. Process memory only (see the module doc).
_pairings: dict[str, _Pairing] = {}
#: Background tasks this module owns (a pairing watcher, a channel restart).
_tasks: set[asyncio.Task[Any]] = set()


def _audit(outcome: str, card_id: str) -> None:
    sel().log_api_access(
        caller="setup_card",
        operation="setup_card.pair",
        outcome=outcome,
        source=TELEGRAM,
        resources=f"card:{card_id}",
    )


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _new_code() -> str:
    return f"{secrets.randbelow(10**PAIR_CODE_DIGITS):0{PAIR_CODE_DIGITS}d}"


def _live(channel: str, card_id: str) -> _Pairing | None:
    pairing = _pairings.get(channel)
    if pairing is None or pairing.card_id != card_id or time.monotonic() >= pairing.deadline:
        return None
    return pairing


def owner_view(card: sc.SetupCard) -> dict[str, Any]:
    """``card.public()`` plus its live pairing code, for the owner-only card routes."""
    view = card.public()
    if card.kind == sc.KIND_CHANNEL and card.status == sc.STATUS_WAITING:
        pairing = _live(str(card.payload.get("channel", "")), card.id)
        if pairing is not None:
            view["outcome"] = {**(card.outcome or {}), "pair_code": pairing.code}
    return view


# ── commit ──────────────────────────────────────────────────────────────────


async def commit_channel(
    state: "DashboardState", card: sc.SetupCard, input_: dict[str, Any]
) -> sc.SetupCard:
    """Store the typed bot token, reconnect the channel and open a pairing.

    Returns the stored card with the code added to its outcome in memory, so
    the decide response and the owner-only card event carry it while the store
    does not.
    """
    channel = str(card.payload.get("channel", ""))
    if channel != TELEGRAM:
        raise sc.CardRejected(
            "connecting this channel from a card is not available yet; use Settings",
            "channel_unavailable",
        )
    token = await _checked_token(input_.get("token"))
    await _store_token(state, token)
    code = _new_code()
    card = await sf._finish(
        card,
        sc.STATUS_WAITING,
        outcome={"channel": channel, "expires_ts": time.time() + PAIR_WAIT_SECS},
    )
    previous = _pairings.get(channel)
    # Registered only once the card is waiting: a /pair can commit nothing else.
    _pairings[channel] = _Pairing(
        card_id=card.id,
        code=code,
        deadline=time.monotonic() + PAIR_WAIT_SECS,
        wrong_left=PAIR_MAX_WRONG,
        state=state,
    )
    if previous is not None and previous.card_id != card.id:
        _spawn(
            _settle(
                previous.state,
                previous.card_id,
                sc.STATUS_EXPIRED,
                error=("pair_superseded", "A newer channel card replaced this pairing code."),
            )
        )
    _spawn(_watch(card.id, channel))
    _audit("waiting", card.id)
    return dataclasses.replace(card, outcome={**(card.outcome or {}), "pair_code": code})


async def _checked_token(raw: Any) -> str:
    """The pasted token, shape-checked and verified the way the Settings save does."""
    from kiro_crew.dashboard.handlers.messaging import (
        _validate_telegram_token,
        clean_telegram_token,
    )

    if not isinstance(raw, str) or not raw.strip():
        raise sc.CardRejected("paste the bot token from @BotFather", "channel_token_empty")
    try:
        token = clean_telegram_token(raw)
    except ValueError:
        raise sc.CardRejected(
            "that does not look like a bot token from @BotFather", "channel_token_invalid"
        ) from None
    try:
        problem = await _validate_telegram_token(token)
    except Exception:
        # Offline is not a bad token: store it, and the channel keeps retrying
        # until Telegram answers. No exc_info: a transport error can carry the
        # request URL, and the URL carries the token.
        logger.warning("Telegram was unreachable; storing the bot token unverified")
        problem = None
    if problem:
        raise sc.CardRejected(f"Telegram rejected this token ({problem})", "channel_token_rejected")
    return token


async def _store_token(state: "DashboardState", token: str) -> None:
    """Store *token*, turn the channel on, and make sure it reconnects with it.

    Turning ``telegram.enabled`` on, or purging a legacy ``telegram.bot_token``,
    is a connection-parameter change the config watcher answers by restarting the
    channel. A token swapped under an already-enabled channel changes only the
    credential file, which the watcher does not read, so that case asks the
    gateway to restart the channel itself.
    """
    from kiro_crew.config.loader import (
        CRED_TELEGRAM_BOT_TOKEN,
        ConfigReadError,
        KiroCrewConfig,
        config_path,
        read_config_for_update,
    )
    from kiro_crew.dashboard.chat_utils import run_to_completion
    from kiro_crew.dashboard.handlers.agents import _get_config_lock
    from kiro_crew.dashboard.handlers.core import _hot_apply_after_write
    from kiro_crew.dashboard.handlers.messaging import commit_telegram_writes

    cfg = await asyncio.to_thread(KiroCrewConfig.load)
    if cfg.telegram.accounts:
        raise sc.CardRejected(
            "telegram.accounts is set, which keeps the Telegram channel off; remove it in "
            "Settings first",
            "channel_accounts_set",
        )

    async def _write() -> bool:
        path = config_path()
        data = await asyncio.to_thread(read_config_for_update, path)
        raw = data.get(TELEGRAM)
        section: dict[str, Any] = raw if isinstance(raw, dict) else {}
        staged: dict[str, object] = {} if section.get("enabled") is True else {"enabled": True}
        await commit_telegram_writes(path, staged, {CRED_TELEGRAM_BOT_TOKEN: token})
        return bool(staged) or bool(section.get("bot_token"))

    try:
        async with _get_config_lock():
            watcher_restarts = await run_to_completion(_write())
    except ConfigReadError:
        raise sc.CardRejected(
            "config.json could not be read, so nothing was stored", "config_unreadable"
        ) from None
    await _hot_apply_after_write()
    restart = getattr(state, "restart_channel", None)
    if not watcher_restarts and callable(restart):
        _spawn(_restart(restart))


async def _restart(restart: Any) -> None:
    try:
        await restart(TELEGRAM)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("restarting the Telegram channel for a setup card failed")


# ── the /pair message ───────────────────────────────────────────────────────


async def telegram_pair_attempt(user_id: str, username: str, code: str) -> str | None:
    """Answer a ``/pair <code>`` DM from *user_id*; ``None`` when no pairing is live.

    The Telegram transport's pair handler (``TelegramTransport._answer_pair``).
    *username* is the sender's ``@handle`` already narrowed by
    ``prompt_safe_handle``, or ``""``. A match is consumed before the first
    await, so a code pairs exactly one sender.
    """
    pairing = _pairings.get(TELEGRAM)
    if pairing is None or time.monotonic() >= pairing.deadline or not user_id.isdigit():
        return None
    if not hmac.compare_digest(code.encode(), pairing.code.encode()):
        pairing.wrong_left -= 1
        if pairing.wrong_left > 0:
            _audit("wrong_code", pairing.card_id)
            return _REPLY_WRONG
        if _pairings.get(TELEGRAM) is pairing:
            del _pairings[TELEGRAM]
        _audit("closed", pairing.card_id)
        await _settle(
            pairing.state,
            pairing.card_id,
            sc.STATUS_FAILED,
            error=("pair_attempts", "Too many wrong pairing codes were sent to the bot."),
        )
        return _REPLY_CLOSED
    del _pairings[TELEGRAM]
    from kiro_crew.dashboard.handlers.messaging import add_telegram_allowed_user

    try:
        await add_telegram_allowed_user(int(user_id))
    except Exception:
        logger.exception("pairing: adding the sender to telegram.allowed_user_ids failed")
        await _settle(
            pairing.state,
            pairing.card_id,
            sc.STATUS_FAILED,
            error=("pair_write_failed", "The Telegram allow-list could not be updated."),
        )
        return _REPLY_FAILED
    _audit("paired", pairing.card_id)
    await asyncio.to_thread(mark_stage, "channel")
    await _settle(
        pairing.state,
        pairing.card_id,
        sc.STATUS_COMMITTED,
        outcome={"channel": TELEGRAM, "paired": True, "username": username},
    )
    return _REPLY_PAIRED


# ── settling a waiting card ─────────────────────────────────────────────────


async def _watch(card_id: str, channel: str) -> None:
    """Expire the card when its code runs out unused."""
    try:
        while True:
            pairing = _pairings.get(channel)
            if pairing is None or pairing.card_id != card_id:
                return  # paired, closed or replaced: whoever removed it settled the card
            remaining = pairing.deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(remaining)
        del _pairings[channel]
        await _settle(
            pairing.state,
            card_id,
            sc.STATUS_EXPIRED,
            error=("pair_timeout", "No /pair message reached the bot in time."),
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("channel card %s pairing watcher failed", card_id)


async def _settle(
    state: "DashboardState",
    card_id: str,
    status: str,
    *,
    outcome: dict[str, Any] | None = None,
    error: tuple[str, str] | None = None,
) -> None:
    """Move a WAITING card to terminal *status*, then show and report it.

    A card that is not waiting any more was settled by someone else and is left as
    it is, so a late watcher cannot overwrite a pairing that just landed.
    """
    settled = False

    def _mutate(c: sc.SetupCard) -> None:
        nonlocal settled
        if c.status != sc.STATUS_WAITING:
            return
        c.status = status
        if outcome is not None:
            c.outcome = outcome
        c.error = {"code": error[0], "message": error[1]} if error else None
        c.decided_ts = time.time()
        settled = True

    try:
        card = await asyncio.to_thread(sc.update_card, card_id, _mutate)
    except sc.CardRejected:
        return
    if not settled:
        return
    sf.broadcast(state, card)
    await sf._report(state, card)
