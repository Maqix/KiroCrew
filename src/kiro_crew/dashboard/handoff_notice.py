"""The main chat hears when a chat it handed work to finishes (RFC §6.9, MC.9).

The main chat hands long work to its own chat (``session_create``, then
``session_send``) and does not wait on it, so without this nothing tells it the
work is done and the user has to ask. When a chat the main chat created ends a
turn and goes idle -- no approval or question waiting, nothing queued, no plan or
sub-agent still working -- the gateway posts ONE deterministic ``handoff_done``
system notice in the main chat. ``meta.outcome`` says how the turn ended:
``done`` when it replied, ``error`` when it ended on an error row with no reply.
A turn someone stopped posts nothing: the stop was pressed in that chat, or by
the main chat through ``session_stop``, so whoever stopped it already knows.

It is not a model turn, so it costs no quota and can raise no setup card (SC8):
the user's "what did it find?" is the turn that reads the chat, with
``session_read_message``.

At most one notice per turn of that chat, and none while the main chat's newest
row is already the same notice, so a user talking in the side chat directly does
not stack them up. A notice due while the main chat is mid-turn or mid-plan is
held until that turn or plan ends, so it never lands between the rows of a
reply; held notices are also kept in the first-run state file, so a restart in
between delivers them after the session restore.

Presentation only, like the main-chat marker it reads: it decides what is shown,
never what a turn may do.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kiro_crew.dashboard.system_notices import HANDOFF_DONE_KIND, is_speech_row

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState, _ChatSlot

logger = logging.getLogger(__name__)

#: ``meta.outcome`` of a notice: the turn replied, or it ended on an error.
OUTCOME_DONE = "done"
OUTCOME_ERROR = "error"
#: The English text of each outcome, the fallback for readers without the catalog.
_TEXT = {
    OUTCOME_DONE: "“{title}” finished. Ask me here for what it found.",
    OUTCOME_ERROR: "“{title}” stopped with an error. Open it to see what happened.",
}
#: Longest chat title a notice quotes; the crew overview's bound.
TITLE_MAX_CHARS = 60
#: Chats whose last noticed turn is remembered; the oldest is forgotten first.
_MAX_TRACKED = 256
#: Most notices held for one main chat. They are also written to the first-run
#: state file, which is read back only while it stays small.
_MAX_HELD = 20
#: Rows that open a turn. The walk back over the turn's rows stops at one.
_TURN_OPENER_ROLES = frozenset({"user", "nudge", "subagent"})


@dataclass
class _Book:
    #: Side chat key -> the turn generation its notice was posted for.
    noticed: dict[str, int] = field(default_factory=dict)
    #: Main chat key -> {side chat key: notice meta}, held while the main chat runs.
    owed: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    #: ``owed`` changed since the state file last recorded it.
    dirty: bool = False
    #: The one task writing ``owed`` to the state file, so writes land in order.
    writer: asyncio.Task[Any] | None = None


#: One book per gateway state. Weak, so a state that is gone takes its book along.
_books: "weakref.WeakKeyDictionary[Any, _Book]" = weakref.WeakKeyDictionary()
#: Delivery and write tasks, held so they are not garbage collected mid-flight.
_tasks: set[asyncio.Task[Any]] = set()


def _book(state: "DashboardState", *, create: bool) -> _Book | None:
    try:
        book = _books.get(state)
        if book is None and create:
            book = _books[state] = _Book()
        return book
    except TypeError:  # a state that cannot be weakly referenced keeps no book
        return None


def _spawn(coro: Any) -> asyncio.Task[Any]:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


def note_cycle_end(state: "DashboardState", slot: "_ChatSlot", *, finished: bool = True) -> None:
    """Called when *slot*'s queue cycle, or its plan, ends and it goes idle.

    Delivers what *slot* is owed as a main chat, then, when *finished*, reports
    *slot* itself if the main chat created it. Returns at once, without touching
    the disk, for a chat nobody created with ``session_create`` and that no notice
    is owed to: every ordinary chat. Never raises, since the turn's end-of-cycle
    work runs after it.
    """
    try:
        creator = str(getattr(slot, "_created_by", "") or "")
        book = _book(state, create=False)
        if book is not None and slot.key in book.owed and not _busy(slot):
            # A main chat whose own turn held notices back; it is idle now.
            for meta in book.owed.pop(slot.key).values():
                _post(state, slot, meta)
            _persist_soon(book)
        if not creator or not finished:
            return
        notice = _finished_notice(state, slot)
        if notice is None:
            return
        generation = int(getattr(slot, "_turn_generation", 0) or 0)
        _spawn(_deliver(state, slot.key, creator, notice, generation))
    except Exception:
        logger.warning("handoff notice check failed for %s", slot.key, exc_info=True)


def note_controller_end(
    state: "DashboardState",
    slot: "_ChatSlot",
    controller: "asyncio.Task[Any] | None",
    *,
    finished: bool,
) -> None:
    """Called when a plan's stage loop lets go of *slot*.

    The loop stays *slot*'s controller, so *slot* reads as running, until its task
    ends; the check therefore runs from that task's done callback. *finished* is
    False for a plan paused on the user: what *slot* is owed is delivered, but
    *slot* itself is not reported done.
    """
    try:
        book = _book(state, create=False)
        if not getattr(slot, "_created_by", "") and not (book and slot.key in book.owed):
            return
        if controller is None or controller.done():
            note_cycle_end(state, slot, finished=finished)
            return
        controller.add_done_callback(lambda _t: note_cycle_end(state, slot, finished=finished))
    except Exception:
        logger.warning("handoff notice check failed for %s", slot.key, exc_info=True)


def _finished_notice(state: "DashboardState", slot: "_ChatSlot") -> dict[str, str] | None:
    """The notice meta for *slot*'s turn that just ended, or ``None``.

    Read synchronously at the cycle end, before the chat can start another turn.
    ``None`` when the chat is not really done (something is queued, an approval or
    a question waits on the user, a plan or a sub-agent is still going), or when
    its turn has nothing to report (see :func:`_turn_outcome`).
    """
    if _busy(slot) or getattr(slot, "_queue", None) or getattr(slot, "_question_pending", None):
        return None
    futures = getattr(slot, "_approval_futures", None) or {}
    if isinstance(futures, dict) and any(not f.done() for f in futures.values()):
        return None
    if getattr(slot, "_subagent_deliveries_inflight", 0):
        return None
    subagents = getattr(state, "subagents", None)
    if subagents is not None:
        try:
            if subagents.running_agents_for(f"dashboard:{slot.key}"):
                return None
        except Exception:
            return None
    outcome = _turn_outcome(slot)
    if outcome is None:
        return None
    return {
        "kind": HANDOFF_DONE_KIND,
        "slot": slot.key,
        "title": _title_of(slot),
        "outcome": outcome,
    }


def _turn_outcome(slot: "_ChatSlot") -> str | None:
    """How the newest turn in *slot* ended, read off its rows.

    ``done`` when it replied, ``error`` when an ``error`` row stands and no reply
    does, ``None`` when it was stopped (a ``stop_event`` row, written by every Stop
    and by ``session_stop``) or left no word at all.
    """
    from kiro_crew.dashboard.state import is_stop_event_row

    replied = errored = False
    for row in reversed(getattr(slot, "messages", None) or []):
        if not isinstance(row, dict):
            continue
        role, meta = row.get("role"), row.get("meta")
        if is_stop_event_row(row):
            return None
        if role == "assistant" and is_speech_row(role, row.get("content"), meta):
            replied = True
        elif role == "error":
            errored = True
        elif role in _TURN_OPENER_ROLES or (
            role == "inject" and isinstance(meta, dict) and meta.get("injectKind")
        ):
            break
    if replied:
        return OUTCOME_DONE
    return OUTCOME_ERROR if errored else None


def _title_of(slot: "_ChatSlot") -> str:
    title = getattr(slot, "display_title", "") or getattr(slot, "title", "") or slot.key
    return _bounded(title)


def _bounded(title: Any) -> str:
    from kiro_crew.dashboard.setup_flow import _plain

    return _plain(title, TITLE_MAX_CHARS)


async def _deliver(
    state: "DashboardState", child_key: str, creator: str, meta: dict[str, str], generation: int
) -> None:
    from kiro_crew.first_run import read_main_slot

    try:
        main = await asyncio.to_thread(read_main_slot)
        if not main or creator != main or child_key == main:
            return
        main_slot = state.get_slot(main)
        book = _book(state, create=True)
        if main_slot is None or book is None or book.noticed.get(child_key) == generation:
            return
        book.noticed.pop(child_key, None)
        book.noticed[child_key] = generation
        while len(book.noticed) > _MAX_TRACKED:
            book.noticed.pop(next(iter(book.noticed)))
        if _busy(main_slot):
            _hold(book, main, meta)
            return
        _post(state, main_slot, meta)
    except Exception:
        logger.warning("handoff notice for %s failed", child_key, exc_info=True)


def _busy(slot: "_ChatSlot") -> bool:
    """Whether a turn or a plan owns *slot*, so a notice would land inside it.

    The RESERVATION (``running``), not execution alone: a dispatched turn that has
    not started streaming yet is still about to write rows.
    """
    return bool(slot.running or slot._in_stage_execution)


def _hold(book: _Book, main: str, meta: dict[str, str]) -> None:
    """Keep *meta* for *main* until it is idle, newest last, and save the hold."""
    held = book.owed.setdefault(main, {})
    held.pop(meta["slot"], None)
    held[meta["slot"]] = meta
    while len(held) > _MAX_HELD:
        held.pop(next(iter(held)))
    _persist_soon(book)


def _persist_soon(book: _Book) -> None:
    """Write ``book.owed`` to the state file, after any write still in flight."""
    book.dirty = True
    if book.writer is None or book.writer.done():
        book.writer = _spawn(_write_held(book))


async def _write_held(book: _Book) -> None:
    from kiro_crew.first_run import write_handoff_owed

    while book.dirty:
        book.dirty = False
        snapshot = {main: dict(held) for main, held in book.owed.items() if held}
        try:
            await asyncio.to_thread(write_handoff_owed, snapshot)
        except Exception:
            logger.warning("held handoff notices were not saved", exc_info=True)
            return


async def restore_held(state: "DashboardState") -> int:
    """Deliver the notices a restart interrupted; returns how many were posted.

    Called once at startup, after the session restore. Notices held for a chat
    that is no longer the main chat are dropped. When the main chat is not open or
    is already running again, they stay held for its next idle moment.
    """
    from kiro_crew.first_run import read_handoff_owed, read_main_slot

    saved = await asyncio.to_thread(read_handoff_owed)
    if not saved:
        return 0
    main = await asyncio.to_thread(read_main_slot)
    book = _book(state, create=True)
    if book is None:
        return 0
    posted = 0
    if main:
        held = book.owed.setdefault(main, {})
        for child, raw in list((saved.get(main) or {}).items())[-_MAX_HELD:]:
            meta = _restored(child, raw)
            if meta is not None:
                held.setdefault(child, meta)
        main_slot = state.get_slot(main)
        if not held:
            book.owed.pop(main, None)
        elif main_slot is not None and not _busy(main_slot):
            for meta in book.owed.pop(main).values():
                posted += _post(state, main_slot, meta)
    # The file now records only what is still held: nothing, or the notices
    # waiting for the main chat.
    _persist_soon(book)
    return posted


def _restored(child: str, raw: dict[str, str]) -> dict[str, str] | None:
    """A held notice read back from the state file, or ``None`` when malformed.

    The file is an ordinary writable file, so the title is bounded again.
    """
    outcome = raw.get("outcome", OUTCOME_DONE)
    if raw.get("kind") != HANDOFF_DONE_KIND or raw.get("slot") != child or outcome not in _TEXT:
        return None
    return {
        "kind": HANDOFF_DONE_KIND,
        "slot": child,
        "title": _bounded(raw.get("title") or child),
        "outcome": outcome,
    }


def _post(state: "DashboardState", main_slot: "_ChatSlot", meta: dict[str, str]) -> bool:
    """Append *meta*'s notice to the main chat unless it is already the newest row.

    Returns whether a row was appended.
    """
    outcome = meta.get("outcome", OUTCOME_DONE)
    for row in reversed(getattr(main_slot, "messages", None) or []):
        if not isinstance(row, dict) or row.get("role") == "done":
            continue
        last = row.get("meta")
        if (
            row.get("role") == "assistant"
            and isinstance(last, dict)
            and last.get("kind") == HANDOFF_DONE_KIND
            and last.get("slot") == meta["slot"]
            and last.get("outcome", OUTCOME_DONE) == outcome
        ):
            return False
        break
    main_slot.append(
        "assistant",
        _TEXT[outcome].format(title=meta["title"]),
        "msg msg-system",
        meta=dict(meta),
    )
    push = getattr(state, "push_slots_update", None)
    if callable(push):
        push()
    logger.info("handoff notice (%s) in %s for %s", outcome, main_slot.key, meta["slot"])
    return True
