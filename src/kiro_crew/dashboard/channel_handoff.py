"""Mid-turn hand-off of a CHANNEL's message into a RESUMED dashboard session's slot.

A channel conversation bound to a dashboard session (``!sessions`` on Discord, the
dashboard's mirror menu) sends its messages into that session. While the session
is mid-turn the channel's OWN mid-turn machinery cannot take them: the channel
queue is drained only at the tail of a turn that channel drove, and its replay
skips resume routing, so a message enqueued there would sit until some later
channel turn and then run in the channel's NATIVE session. The dashboard slot has
its own steer path (the injection the composer uses) and its own queue (drained
by the dashboard turn loop, so ordering is the dashboard's). This module hands the
message to those, and says when it cannot.

It lives in the dashboard package because it drives the dashboard's delivery
seams; ``messaging`` may not import the dashboard, and a channel dispatcher
reaches this module through a deferred import, the way it reaches the live-slot
projection.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from kiro_crew.dashboard.chat_delivery import (
    MAX_PENDING_STEERS,
    STEER_REQUEUED,
    STEER_STEERED,
    queue_for_next_turn,
    sanitize_outbound,
    steer_into_running_turn,
)
from kiro_crew.messaging.upload_gate import live_dashboard_slot

logger = logging.getLogger(__name__)


#: The message cut into the slot's running turn.
HANDOFF_STEERED = "steered"
#: The message waits in the slot's queue for the dashboard turn loop to drain.
HANDOFF_QUEUED = "queued"
#: The slot could not take the message; ``reason`` says why.
HANDOFF_REFUSED = "refused"

# Why a hand-off was refused. The channel words each for its own surface.
#: The key resolves to no open dashboard slot (no tab, or not a ``dashboard:`` key).
REFUSED_NO_SLOT = "no_slot"
#: An incognito or temporary session: both arms write its transcript.
REFUSED_RESTRICTED = "restricted"
#: The slot's teardown has fenced admission.
REFUSED_CLOSING = "closing"
#: The slot runs its turns on a remote crew, which has no local drain.
REFUSED_REMOTE = "remote"
#: The lease is held, but not by the dashboard turn loop: the slot itself is idle.
REFUSED_IDLE = "idle"
#: The message carries attachments, which neither arm can carry.
REFUSED_ATTACHMENTS = "attachments"
#: The key stopped resolving to the slot the steer was handed to while the RPC
#: was suspended: the slot was closed, or closed and recreated under the same key.
REFUSED_MOVED = "moved"
#: The slot's audience-fence set is at its cap and this message names a new
#: audience. Refused rather than recorded over an existing fence: a fence dropped
#: is a cross-surface leg published that should have been withheld.
REFUSED_FENCE_CAP = "fence_cap"

# Where the text stands once the key stopped resolving to the slot it was handed
# to (:func:`text_after_move`). Carried as a QUEUED outcome's ``reason`` so the
# channel can word each one; the stranded case is ``REFUSED_MOVED``.
#: The successor slot under the same key holds the text in its queue.
QUEUED_ON_SUCCESSOR = "successor_queue"
#: The successor slot under the same key ran (or is running) the text as its own turn.
RAN_ON_SUCCESSOR = "successor_turn"
#: The slot is closing and its turn's teardown owes the text to the queue the
#: close persists, so the text runs when the session is next resumed.
QUEUED_BY_CLOSE = "closing_queue"


@dataclass(frozen=True)
class ResumedBusyOutcome:
    """What became of one mid-turn message handed to a resumed session's slot."""

    kind: str
    reason: str = ""

    @property
    def refused(self) -> bool:
        return self.kind == HANDOFF_REFUSED


def _refused(reason: str) -> ResumedBusyOutcome:
    return ResumedBusyOutcome(HANDOFF_REFUSED, reason)


#: Hand-offs in flight, held STRONGLY. A hand-off runs as its own task, awaited
#: through ``asyncio.shield`` so the channel handler's cancellation cannot cut it
#: off between the steer RPC and its reconciliation (see
#: :func:`hand_to_resumed_slot`). The loop keeps only weak references to its
#: tasks, and once the awaiter is cancelled and unwinds nothing else names this
#: one, so this set is what keeps it alive to finish; each task discards itself
#: on completion.
_HANDOFFS_IN_FLIGHT: set[asyncio.Task[ResumedBusyOutcome]] = set()


def audience_fence_key(admission: dict[str, Any]) -> str:
    """The fence key for *admission*: the AUDIENCE it names, not the message.

    ``slot._steer_audience_fences`` is retained for the whole turn (its teardown
    clears it), while every other per-steer map empties as the turn consumes the
    steer -- so a record per message would grow for as long as the turn runs.
    Keyed by the containment snapshot instead, one turn holds one record per
    distinct audience however many messages a channel sends into it, and
    re-recording the same audience is a no-op. A stable digest of the snapshot,
    since the publisher compares the snapshot's content and two admissions that
    agree on it are one fence. Deterministic on purpose: the peer path's keys are
    random tokens (one per delivery, popped by the sender), and a channel's
    audience keys never collide with them.
    """
    from kiro_crew.dashboard.session_control import QUEUED_CONTAINMENT_META_KEY

    snapshot = admission.get(QUEUED_CONTAINMENT_META_KEY)
    encoded = json.dumps(snapshot, sort_keys=True, default=str).encode("utf-8")
    return "audience:" + hashlib.sha256(encoded).hexdigest()[:24]


def slot_unable_to_take(slot: Any) -> str:
    """The ``REFUSED_*`` reason *slot* cannot take a mid-turn message, or ``""``.

    Every read fails CLOSED on an attribute the slot does not carry: a slot that
    cannot say it is unrestricted, or that it is running, is refused rather than
    written to.

    ``running or _in_stage_execution`` is the predicate every producer that must
    not start a concurrent turn reads. The lease the channel observed as busy can
    be held by something other than the dashboard turn loop -- the channel's own
    turn on the resumed key is the live case -- and then the slot has no published
    client to steer into and no drain coming: a queue entry would strand until an
    unrelated later dashboard turn, and the queue-or-run admission would START a
    turn against a lease another driver holds.
    """
    if slot is None:
        return REFUSED_NO_SLOT
    if getattr(slot, "is_restricted", True):
        return REFUSED_RESTRICTED
    if getattr(slot, "is_closing", False):
        return REFUSED_CLOSING
    if getattr(slot, "is_remote", False) or getattr(slot, "executor", "") == "remote":
        return REFUSED_REMOTE
    if not (getattr(slot, "running", False) or getattr(slot, "_in_stage_execution", False)):
        return REFUSED_IDLE
    return ""


def _queue_holds(slot: Any, text: str) -> bool:
    return any(
        isinstance(entry, dict) and entry.get("content") == text
        for entry in (getattr(slot, "_queue", None) or [])
    )


def _turn_record_holds(slot: Any, text: str) -> bool:
    """Whether *slot*'s transcript carries a user row for *text*.

    The queue drain appends the entry's content as the turn's user row (a channel
    author's may be stored display-safe, so both forms count), which is the record
    that the text ran, or is running, as its own turn on that slot.
    """
    forms = {text, sanitize_outbound(text)}
    for row in reversed(getattr(slot, "messages", None) or []):
        if isinstance(row, dict) and row.get("role") == "user" and row.get("content") in forms:
            return True
    return False


def text_after_move(slot: Any, successor: Any, text: str) -> str:
    """Where *text* stands once its key stopped resolving to *slot*.

    Read from the queue and turn RECORDS, never from timing: the slot the text was
    handed to and the slot the key resolves to now (``None`` when the session is
    closed and not reopened). Returns a ``QUEUED_*``/``RAN_*`` reason when the
    text will run or ran, and ``REFUSED_MOVED`` when nothing holds it.

    * A live successor is the only object whose queue a drain reaches, so the text
      is queued when the successor's queue holds it, and delivered when the
      successor's transcript shows it ran there. An entry sitting on the detached
      object while a successor is live is stranded: the close hands the
      replacement nothing from that queue.
    * No successor means the slot is closing. The close archives the popped slot
      WITH its queue (``queued_prompts``), so a text the turn's teardown owes to
      that queue -- still pending, or already requeued -- runs when the session is
      next resumed. Reading the popped object is reading the record the close is
      about to persist; a save failure puts the slot back live with the same
      queue. A text the teardown does not hold (a declined steer's unwound
      registration, a hard stop's cleared pending list) is stranded.
    """
    if successor is not None:
        if _queue_holds(successor, text):
            return QUEUED_ON_SUCCESSOR
        if _turn_record_holds(successor, text):
            return RAN_ON_SUCCESSOR
        return REFUSED_MOVED
    if text in (getattr(slot, "_pending_steers", None) or []) or _queue_holds(slot, text):
        return QUEUED_BY_CLOSE
    return REFUSED_MOVED


async def hand_to_resumed_slot(
    state: Any,
    session_key: str,
    text: str,
    *,
    mode: str,
    has_attachments: bool,
    channel_type: str = "",
    conversation_id: str = "",
    principal: str = "",
) -> ResumedBusyOutcome:
    """Route *text*, sent mid-turn into resumed *session_key*, to its slot.

    *state* is the channel dispatcher's ``dashboard_state`` handle: the gateway's
    ``DashboardState`` when a dashboard is attached, ``None`` when none is, which
    resolves no slot and is refused like any other missing slot.

    *channel_type*, *conversation_id* and *principal* name the conversation the
    text came from and the platform user the channel authorized on inbound. They
    are stamped on whatever the text becomes -- the queue entry directly, the steer
    through its admission dict, which the requeue copies onto the entry
    (``session_control.channel_recipient_meta``) -- so a drain-time drop of the
    queued text is reported back into that conversation
    (``session_control.notify_channel_recipient_dropped``): the conversation was
    told "queued" and reads neither the target's transcript nor the SEL. Left
    empty, nothing is stamped and a drop is reported nowhere, as for a
    dashboard-typed entry.

    *mode* is the channel's resolved mid-turn mode (``"steer"`` or ``"queue"``,
    per-message override already applied). Steer is attempted only when asked for
    AND the slot's published client can take a steer for THIS author; every other
    case, including a steer the client declines, takes the slot's queue so the text
    is never dropped.

    The text is handed over as the human's own words -- no provenance envelope,
    because the author IS the session's own human: a channel may resume a
    dashboard session only under its owner gate. What the two arms record differs
    by where the text runs:

    * A steer runs INSIDE the dashboard's turn, under that turn's provenance, as
      every steer does (the composer's and a peer's alike). What it records is the
      same audience fence the peer path (``session_control.send_to_target``)
      records: the containment holding at admission, kept on the slot for the
      whole turn, so the publisher's ``cross_surface_withheld`` withholds the
      reply's cross-surface leg when a constraint newly holds. The record lands
      BEFORE the RPC, because ``steer()`` suspends and a fast turn can publish
      before it returns. It is keyed by the AUDIENCE (:func:`audience_fence_key`)
      and bounded: one record per distinct containment snapshot per turn however
      many messages the channel sends, re-recording the same audience is a
      no-op, the set shares ``MAX_PENDING_STEERS`` with the other per-steer stores
      (a new audience at the cap is REFUSED, ``REFUSED_FENCE_CAP``, never recorded
      over an existing fence), and the turn's teardown clears it. The record stays when the text did not enter the turn:
      it is the audience's, another steer under it may have landed meanwhile, and
      while the audience is unchanged it costs the reply nothing, since the
      publisher's comparison is exact. The peer path's containment STOP is not
      repeated here: that stop narrows a delivery a gate authorized against
      containment, and there is no such gate on a human's own message -- the fence
      alone is what protects publication.
    * A queued message runs as its OWN turn, so it carries the provenance a
      channel human's text carries on the Slack linked-thread path: user origin
      (the session's own human typed it, which is what earns the LINKED exemption
      at the drain) and channel origin (channel authority is the narrower
      credential boundary, so a directive that turn issues is filed as
      channel-created). A steer that ends up requeued carries the same two marks
      through the slot's per-steer maps. Both marks together also keep the row and
      the card display-redacted: a channel author is not the dashboard's reader.

    The steer RPC suspends, and the slot can move under it. After the RPC the key
    is resolved AGAIN and compared by object identity, the way
    ``session_control.send_to_target`` re-gates its fallback: a slot closed and
    recreated under the same key compares equal by key while the queue the text
    would land on belongs to a detached object no drain will ever reach. A moved
    slot is not refused outright: the text may already be somewhere that runs it
    -- a close during the RPC cancels the turn, whose teardown requeues the
    pending steer onto the queue the close archives, so the text runs when the
    session is next resumed -- and a refusal there reads as NOT delivered to a
    human who then resends and runs it twice. :func:`text_after_move` reads the
    queue and turn records of both objects (the one the text was handed to, the
    one the key resolves to now) and the outcome follows them: queued on the
    successor, ran on the successor, queued by the close, or -- when nothing holds
    the text -- refused (``REFUSED_MOVED``). The fallback never appends to the
    detached object, and on an unmoved slot it re-runs the admission gate, because
    a slot that went idle or started closing during the RPC cannot take the text
    any more.

    Attachments are refused rather than queued without: ``_session/steer`` carries
    text only, and the slot's queue cannot carry channel attachment material -- it
    is downloaded into temp files owned by the consuming turn, and the dashboard
    drain has no hook to own them. Refusing keeps the files with the user.

    The whole hand-off -- gate, steer RPC, reconciliation, queue fallback -- runs
    as ONE task, held by a strong reference (``_HANDOFFS_IN_FLIGHT``) and awaited
    through ``asyncio.shield``. The caller is a channel's message handler, and a
    transport close cancels those handlers as an ordinary path (Discord gathers
    its handler tasks on close). Awaited inline, that cancellation lands inside
    ``steer_into_running_turn``'s RPC: the pending registration is made, the
    client may already have accepted the text, and everything behind the RPC --
    the transcript row for an accepted steer, the unwind of a declined one, the
    fallback to the queue -- is skipped, so accepted text runs with no row while
    the per-steer maps keep its entry for the slot's lifetime (the
    ``steering_consumed`` settle removes the pending entry and patches an existing
    row; the turn's teardown requeues only UNconsumed steers). Shielded, the
    caller's cancellation cancels the shield's outer future alone: the hand-off
    task completes the RPC, the reconciliation and the fallback, and the caller
    unwinds without the outcome (its confirmation has no live transport to go to).
    The strong reference is released by the task's done callback on every
    completion -- return, error, or cancellation of the task itself, which only
    the loop's own shutdown performs, when the turn the text was written into is
    going down with it. The audience fence recorded before the RPC is not this
    task's to release: it is the audience's record for the turn (above), and the
    turn's teardown clears it.
    """
    loop = asyncio.get_running_loop()
    task = loop.create_task(
        _run_handoff(
            state,
            session_key,
            text,
            mode=mode,
            has_attachments=has_attachments,
            channel_type=channel_type,
            conversation_id=conversation_id,
            principal=principal,
        ),
        name=f"channel-handoff:{session_key}",
    )
    _HANDOFFS_IN_FLIGHT.add(task)
    task.add_done_callback(_HANDOFFS_IN_FLIGHT.discard)
    return await asyncio.shield(task)


async def _run_handoff(
    state: Any,
    session_key: str,
    text: str,
    *,
    mode: str,
    has_attachments: bool,
    channel_type: str,
    conversation_id: str,
    principal: str,
) -> ResumedBusyOutcome:
    """The hand-off proper; :func:`hand_to_resumed_slot` runs it as a shielded task."""
    slot = live_dashboard_slot(state, session_key)
    blocked = slot_unable_to_take(slot)
    if blocked or slot is None:
        # ``slot is None`` is already ``REFUSED_NO_SLOT`` above; restated so the
        # slot reads as present from here on.
        return _refused(blocked or REFUSED_NO_SLOT)
    if has_attachments:
        return _refused(REFUSED_ATTACHMENTS)

    # circular import: session_control imports this package's modules at module level.
    from kiro_crew.dashboard.session_control import (
        CHANNEL_RECIPIENT_META_KEY,
        channel_recipient_meta,
        containment_meta,
    )

    recipient = channel_recipient_meta(channel_type, conversation_id, principal)

    if mode != "queue":
        client = getattr(slot, "_acp_client", None)
        # codex can drop a steer it already took when a later approval in the
        # turn is denied. The composer accepts that because its human watches the
        # turn and can resend; a channel human cannot see the dashboard's turn,
        # so the text takes the queue instead of an injection that may vanish.
        if getattr(client, "steer_needs_loss_recovery", False) is not True:
            # The recipient rides the admission dict, as the peer path's sender
            # stamp does: the requeue copies that dict onto the entry verbatim,
            # so a steer that ends up queued keeps its drop-notice address. Inert
            # for the fence and the drain, which read only the containment key.
            admission = {**containment_meta(state, slot), **recipient}
            # The audience fence, recorded BEFORE the RPC (see the docstring):
            # one record per audience per turn, and the set shares the cap every
            # other per-steer store has. At the cap -- an audience that changed
            # MAX_PENDING_STEERS times inside one turn -- a NEW audience is
            # refused, and no recorded fence is evicted or overwritten to make
            # room: a fence dropped is a cross-surface leg published that should
            # have been withheld, which is worse than one message the author
            # still holds and can resend.
            fence = audience_fence_key(admission)
            fences = slot._steer_audience_fences
            if fence not in fences and len(fences) >= MAX_PENDING_STEERS:
                logger.warning(
                    "channel hand-off: audience fence cap reached for slot %s (%d); "
                    "refusing the message rather than evicting a fence",
                    getattr(slot, "key", "?"),
                    MAX_PENDING_STEERS,
                )
                return _refused(REFUSED_FENCE_CAP)
            fences.setdefault(fence, admission)
            outcome = await steer_into_running_turn(
                state,
                slot,
                text,
                user_origin=True,
                channel_origin=True,
                admission=admission,
            )
            # The fence record STAYS whatever happened to the text. It is the
            # audience's record, not the message's: another steer under the same
            # audience may have landed while this RPC was suspended and reads it,
            # and while the audience is unchanged it costs the reply nothing (the
            # publisher's comparison is exact). The same conservative direction a
            # cancelled peer delivery takes.
            successor = live_dashboard_slot(state, session_key)
            if successor is not slot:
                # Object identity, not key equality: the key resolves to a fresh
                # object, or to nothing. Checked before reading ``outcome``, for
                # every outcome: an accepted steer into a turn the close has
                # cancelled is not "steering", and the teardown that requeues it
                # runs in another coroutine, so ``outcome`` alone cannot say where
                # the text stands. The records can (:func:`text_after_move`).
                standing = text_after_move(slot, successor, text)
                if standing == REFUSED_MOVED:
                    logger.warning(
                        "channel hand-off: %s stopped resolving to the slot the steer was "
                        "handed to and nothing holds the text; refused rather than queued "
                        "onto the detached object",
                        session_key,
                    )
                    return _refused(REFUSED_MOVED)
                logger.info(
                    "channel hand-off: %s moved while the steer was in flight; the text "
                    "stands as %s",
                    session_key,
                    standing,
                )
                return ResumedBusyOutcome(HANDOFF_QUEUED, standing)
            if outcome == STEER_STEERED:
                return ResumedBusyOutcome(HANDOFF_STEERED)
            if outcome == STEER_REQUEUED:
                # The turn ended while the RPC was suspended and its teardown moved
                # the text onto the queue: it WILL run, and queueing it again here
                # would run it twice.
                return ResumedBusyOutcome(HANDOFF_QUEUED)
            # STEER_UNAVAILABLE: no steer-capable client, an RPC that lost the
            # text, or an identical steer already in flight. Nothing holds the
            # text, so the queue arm below takes it -- against the admission gate
            # re-run on the far side of the suspension.
            blocked = slot_unable_to_take(slot)
            if blocked:
                return _refused(blocked)
    queue_for_next_turn(
        state,
        slot,
        text,
        directive_user_origin=True,
        directive_channel_origin=True,
        channel_recipient=recipient.get(CHANNEL_RECIPIENT_META_KEY),
    )
    return ResumedBusyOutcome(HANDOFF_QUEUED)
