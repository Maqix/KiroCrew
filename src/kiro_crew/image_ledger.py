"""Per-session inline-image ledger: dedup and an aggregate budget over prompt blocks.

The per-image caps in :mod:`kiro_crew.imaging` bound ONE image. Nothing there
bounds what a session inlines in total, and the total is what the backend
measures: kiro-cli replays the whole conversation to the model on every turn, so
every inlined image is re-sent on every later turn and the request body grows by
that image's full base64 size per turn until the backend refuses the body. The
context window is not what gives out -- usage read 3.6% when a request was
refused -- the wire bytes are. This module is the layer over the FINISHED block
list that bounds that growth, whatever produced the blocks:

* **Dedup.** Every inlined image is keyed by the SHA-256 of its base64 payload
  and the keys are kept per session. A payload already inlined in this session
  is not inlined again -- the conversation the model sees already carries it --
  and its text marker becomes ``[image: <name>, sent earlier]``. This holds for
  an automation that names the same file every cycle and for a person who
  pastes the same screenshot twice.
* **Budget.** A per-prompt cap on image count and on total base64 bytes, plus a
  per-session running total of inlined base64 bytes. A block that would cross
  any of them is degraded to a text marker that keeps the file path, so a
  tool-capable agent can still open the file.

The ledger lives on the session's durable record -- the ``SessionMap`` entry,
reached through the store the session manager registers here -- so it survives
a gateway restart, and it names the native conversation it describes: it carries
the ACP session id (``sid``) its images were inlined into, and a prompt on a
different sid reads it as empty, because a new native conversation (``/new``, a
discarded conversation, a provider switch, a fresh session whose sid promotion is
deferred behind a history replay) carries none of the old images. A session with
no durable record (a stateless cron or subagent session, the direct client)
keeps an in-memory ledger for the life of its handle.

A LEAF module, like :mod:`kiro_crew.imaging`: it imports nothing from
``kiro_crew.acp`` (which imports it) and nothing from ``kiro_crew.session_map``
(which implements :class:`ImageLedgerStore`), so both sides can import it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: Smallest request-body ceiling measured so far on a backend route, as bytes of
#: base64 the request carried. One route accepted a request replaying 9 copies
#: of a 3,380,356-byte base64 image (30.4 MB) and refused the 10th (33.8 MB)
#: with ``Improperly formed request``; 32 MiB (33,554,432) lies inside that
#: bracket. A second route accepted 135 MB and kept going, so this is the
#: SMALLEST ceiling seen, and every budget below is a share of it -- an image
#: that fits under it fits on every route measured.
_SMALLEST_MEASURED_REQUEST_BODY_CEILING = 32 * 1024 * 1024

#: Per-session running total of inlined base64 bytes: three quarters of the
#: smallest measured ceiling. The remaining quarter is what the same replayed
#: request carries besides images -- the conversation text, tool results and
#: JSON framing -- so the images alone can never bring the body to the ceiling.
MAX_SESSION_IMAGE_B64_BYTES = _SMALLEST_MEASURED_REQUEST_BODY_CEILING * 3 // 4

#: Per-prompt total of inlined base64 bytes: half the session allowance, so one
#: prompt cannot spend the whole session budget and leave nothing for the
#: screenshot the next question needs. This cap holds even where no ledger is
#: available (nothing persisted yet, the first prompt of a session).
MAX_PROMPT_IMAGE_B64_BYTES = MAX_SESSION_IMAGE_B64_BYTES // 2

#: Per-prompt image count. An inlined image costs about 1,600 tokens of the
#: window whatever its pixel count (measured on a 1M-token window: every image
#: moved the context meter by the same 0.16%, a 2000x1200 frame and a 921x972
#: one alike), and the replay re-spends that on every later turn, so 20 images
#: are ~32k tokens per turn for the rest of the conversation. It is also the
#: count above which the backend applies the many-image dimension rule that
#: ``kiro_crew.imaging.MAX_IMAGE_EDGE_PX`` documents.
MAX_PROMPT_IMAGE_BLOCKS = 20

#: Bound on the digests a ledger retains, oldest evicted first. The byte total
#: is the hard bound on growth; the digest list only decides which repeats are
#: recognised, and an evicted digest costs one re-inline of that image, never a
#: wrong dedup. Each digest is a fixed 64-hex-character SHA-256, so the list is
#: at most 16 KiB on the session record.
MAX_LEDGER_HASHES = 256

#: The only shape a retained digest may have: the lowercase hex SHA-256 that
#: :func:`image_digest` produces. Anything else in a record -- a wrong length, an
#: uppercase or non-hex character -- is dropped at retention, so a malformed
#: entry can neither occupy a slot nor evict a real digest past the bound.
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")

#: Host-side annotation the prompt builder attaches to each image block::
#:
#:     {"path": <the path as written>, "spans": [[s, e], ...]}
#:
#: ``path`` is what the degraded marker names so a tool-capable agent can still
#: open the file. ``spans`` are the ``[start, end)`` offsets, in the prompt's
#: first text block, of every marker the builder wrote for THIS block -- the
#: substitutions it performed, not a search for their text -- so the layer
#: rewrites exactly those characters when it drops the block and never a
#: neighbour's marker, nor a bracketed string the user happened to type.
#: Stripped -- with every other ``_``-prefixed key -- before the list is
#: returned, so it never reaches the wire. A block without the annotation is
#: still deduped and budgeted; its dropped image is reported with a generic note
#: appended to the text instead.
IMAGE_BLOCK_SOURCE_KEY = "_source"

#: Notes appended to the text for a degraded block whose marker cannot be
#: rewritten in place, so the model is told an image was dropped regardless.
UNNAMED_SENT_EARLIER_NOTE = "[image omitted: sent earlier in this session]"
UNNAMED_OVER_BUDGET_NOTE = "[image omitted: over the image budget]"

_REASON_SENT_EARLIER = "sent_earlier"
_REASON_OVER_BUDGET = "over_budget"


class ImageLedgerStore(Protocol):
    """The durable home of a session's ledger -- ``SessionMap`` implements it."""

    def get_image_ledger(self, key: str) -> dict[str, Any] | None:
        """The ledger stored for *key*, ``None`` when *key* has no durable record."""

    def set_image_ledger(self, key: str, ledger: dict[str, Any]) -> bool:
        """Store *ledger* on *key*'s EXISTING record; ``False`` when there is none."""


# The live store, registered by the session manager that owns the live
# SessionMap. MODULE-level for the same reason the map's own listeners are: the
# prompt path runs inside the ACP layer, which holds a session KEY and nothing
# that reaches the manager, and a throwaway ``SessionMap()`` is read-only by
# that class's contract -- only the live instance may write.
_STORE: ImageLedgerStore | None = None


def set_image_ledger_store(store: ImageLedgerStore | None) -> None:
    """Register (or clear, with ``None``) the durable ledger store."""
    global _STORE
    _STORE = store


def empty_ledger(sid: str = "") -> dict[str, Any]:
    """A ledger for native conversation *sid* that has inlined nothing."""
    return {"sid": sid, "hashes": [], "b64_bytes": 0}


def normalize_ledger(raw: object) -> dict[str, Any]:
    """*raw* as a well-formed ledger, dropping anything a ledger cannot hold.

    Applied at every point of retention -- when a ledger is read back from the
    session record and again before one is stored -- so a hand-edited or
    corrupt record can neither grow the list past :data:`MAX_LEDGER_HASHES`
    nor retain a string that is not a digest. Anything malformed reads as an
    empty ledger, which only ever costs a re-inline. The ``sid`` is kept as
    given (or ``""``); the session record bounds its length at retention with
    the one ACP-session-id bound the map already applies to every sid it holds.
    """
    if not isinstance(raw, dict):
        return empty_ledger()
    hashes_raw = raw.get("hashes")
    hashes = (
        [h for h in hashes_raw if isinstance(h, str) and _DIGEST_RE.fullmatch(h)]
        if isinstance(hashes_raw, list)
        else []
    )
    b64_raw = raw.get("b64_bytes")
    b64_bytes = b64_raw if isinstance(b64_raw, int) and not isinstance(b64_raw, bool) else 0
    sid_raw = raw.get("sid")
    return {
        "sid": sid_raw if isinstance(sid_raw, str) else "",
        "hashes": hashes[-MAX_LEDGER_HASHES:],
        "b64_bytes": max(0, b64_bytes),
    }


def load_image_ledger(session_key: str, session_id: str) -> dict[str, Any] | None:
    """The durable ledger for *session_key*'s conversation *session_id*, or ``None``.

    ``None`` means the session has no durable record and the caller must fall
    back to an in-memory ledger. A record that describes a DIFFERENT native
    conversation -- the entry still carries the previous sid's ledger because a
    fresh session's sid promotion is deferred behind a history replay, or the
    record predates the sid -- reads as an empty ledger for *session_id*: the
    new conversation carries none of the old images, and treating it otherwise
    would drop a picture attached to a conversation that never received it.
    """
    store = _STORE
    if not session_key or store is None:
        return None
    raw = store.get_image_ledger(session_key)
    if raw is None:
        return None
    ledger = normalize_ledger(raw)
    return ledger if ledger["sid"] == session_id else empty_ledger(session_id)


def store_image_ledger(session_key: str, ledger: dict[str, Any]) -> bool:
    """Persist *ledger* for *session_key*; ``False`` when nothing durable took it.

    A failed write is reported, never raised: the prompt it belongs to is
    already built, and losing one ledger update costs at most one re-inline on
    the next turn.
    """
    store = _STORE
    if not session_key or store is None:
        return False
    try:
        return bool(store.set_image_ledger(session_key, normalize_ledger(ledger)))
    except OSError:
        logger.warning("image ledger: could not persist for session %s", session_key, exc_info=True)
        return False


def _is_image_block(block: object) -> bool:
    return (
        isinstance(block, dict)
        and block.get("type") == "image"
        and isinstance(block.get("data"), str)
    )


def _is_text_block(block: object) -> bool:
    return (
        isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    )


def has_image_blocks(blocks: list[dict[str, Any]]) -> bool:
    """Whether *blocks* carries at least one image block the layer would judge."""
    return any(_is_image_block(b) for b in blocks)


def image_digest(data: str) -> str:
    """SHA-256 of an image block's base64 payload, the ledger's key.

    Hashed as the base64 text rather than the decoded bytes: base64 is a fixed
    bijection, so two blocks share a digest exactly when they would put the same
    bytes on the wire, and no decode buffer is allocated per image per turn.
    """
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ImageBudgetResult:
    """What :func:`apply_image_budget` decided, plus the ledger to carry forward."""

    blocks: list[dict[str, Any]]
    ledger: dict[str, Any]
    inlined: int
    sent_earlier: int
    over_budget: int
    evicted: int


def apply_image_budget(
    blocks: list[dict[str, Any]], ledger: dict[str, Any] | None
) -> ImageBudgetResult:
    """Dedup and budget the image blocks in *blocks* against *ledger*.

    A pure function over the finished block list and the session's ledger: it
    reads only ``type``, ``data`` and the optional
    :data:`IMAGE_BLOCK_SOURCE_KEY` annotation the builder writes. Blocks are judged in
    order. An image whose digest the ledger already holds -- or that an earlier
    block of this same prompt already inlined -- is dropped and its marker
    rewritten to ``[image: <name>, sent earlier; file: <path>]``. Otherwise the
    block is kept only while the prompt stays within :data:`MAX_PROMPT_IMAGE_BLOCKS`
    and :data:`MAX_PROMPT_IMAGE_B64_BYTES` and the session total stays within
    :data:`MAX_SESSION_IMAGE_B64_BYTES`; a block that would cross any of them is dropped and
    its marker rewritten to ``[image: <name>, not inlined: over the image
    budget; file: <path>]``. Only KEPT blocks enter the ledger: a block the
    budget refused was never sent, so a later prompt may still inline it. The
    returned ledger keeps the input ledger's ``sid``.

    Returns fresh objects and mutates neither input. Kept image blocks come back
    without any ``_``-prefixed key, and non-image blocks pass through unchanged
    except for the text rewritten for a degraded image.
    """
    state = normalize_ledger(ledger)
    known: set[str] = set(state["hashes"])
    hashes: list[str] = list(state["hashes"])
    session_bytes: int = state["b64_bytes"]

    out: list[dict[str, Any]] = []
    degraded: list[tuple[dict[str, Any], str]] = []
    inlined = 0
    prompt_bytes = 0
    sent_earlier = 0
    over_budget = 0
    for block in blocks:
        if not _is_image_block(block):
            out.append(block)
            continue
        data: str = block["data"]
        digest = image_digest(data)
        source = block.get(IMAGE_BLOCK_SOURCE_KEY)
        annotation = source if isinstance(source, dict) else {}
        if digest in known:
            sent_earlier += 1
            degraded.append((annotation, _REASON_SENT_EARLIER))
            continue
        size = len(data)
        if (
            inlined + 1 > MAX_PROMPT_IMAGE_BLOCKS
            or prompt_bytes + size > MAX_PROMPT_IMAGE_B64_BYTES
            or session_bytes + size > MAX_SESSION_IMAGE_B64_BYTES
        ):
            over_budget += 1
            degraded.append((annotation, _REASON_OVER_BUDGET))
            continue
        known.add(digest)
        hashes.append(digest)
        inlined += 1
        prompt_bytes += size
        session_bytes += size
        out.append({k: v for k, v in block.items() if not str(k).startswith("_")})

    if degraded:
        out = _rewrite_markers(out, degraded)
    evicted = max(0, len(hashes) - MAX_LEDGER_HASHES)
    return ImageBudgetResult(
        blocks=out,
        ledger={
            "sid": state["sid"],
            "hashes": hashes[-MAX_LEDGER_HASHES:],
            "b64_bytes": session_bytes,
        },
        inlined=inlined,
        sent_earlier=sent_earlier,
        over_budget=over_budget,
        evicted=evicted,
    )


def _degraded_text(marker: str, annotation: dict[str, Any], reason: str) -> str:
    """The text that replaces *marker* (the producer's own ``[image: ...]``) for a dropped block.

    Keeps the marker's bracketed text and appends the reason -- and the file
    path, when the annotation carries one. The path rides along even for a
    repeat: a native compaction inside one conversation does not reset the
    ledger, so after one the picture may be out of the model's context, and the
    path keeps the file reachable to a tool-capable agent. For a block over the
    budget the path is the builder's own fallback for an image it cannot inline.
    """
    path = annotation.get("path")
    has_path = isinstance(path, str) and bool(path)
    if reason == _REASON_SENT_EARLIER:
        suffix = f", sent earlier; file: {path}]" if has_path else ", sent earlier]"
    else:
        suffix = (
            f", not inlined: over the image budget; file: {path}]"
            if has_path
            else ", not inlined: over the image budget]"
        )
    return marker[:-1] + suffix


def _rewrite_markers(
    blocks: list[dict[str, Any]], degraded: list[tuple[dict[str, Any], str]]
) -> list[dict[str, Any]]:
    """Rewrite each degraded image's own markers, at the offsets its producer recorded.

    Only the characters the builder substituted are touched -- the spans the
    annotation carries, applied right to left so earlier offsets stay valid --
    never a search for the marker's text, which would also rewrite a bracketed
    string the user typed or a neighbour's identical marker. The spans are the
    builder's own substitution record for the prompt's first text block, computed
    in the same pass that wrote the markers, so they are used as given. A degraded
    block that carries no annotation is reported with a generic note appended to
    the last text block (or a new text block when there is none). Text blocks are
    copied before they are edited, so the caller's list is never mutated.
    """
    out = [dict(b) if _is_text_block(b) else b for b in blocks]
    text_indexes = [i for i, b in enumerate(out) if _is_text_block(b)]
    first = text_indexes[0] if text_indexes else None
    edits: list[tuple[int, int, str]] = []
    notes: list[str] = []
    for annotation, reason in degraded:
        spans = annotation.get("spans") if first is not None else None
        if first is None or not spans:
            notes.append(
                UNNAMED_SENT_EARLIER_NOTE
                if reason == _REASON_SENT_EARLIER
                else UNNAMED_OVER_BUDGET_NOTE
            )
            continue
        text = out[first]["text"]
        for start, end in spans:
            edits.append((start, end, _degraded_text(text[start:end], annotation, reason)))
    if edits and first is not None:
        text = out[first]["text"]
        for start, end, replacement in sorted(edits, reverse=True):
            text = text[:start] + replacement + text[end:]
        out[first]["text"] = text
    if notes:
        note_text = "\n".join(notes)
        if text_indexes:
            i = text_indexes[-1]
            existing = out[i]["text"]
            out[i]["text"] = f"{existing.rstrip()}\n{note_text}" if existing.strip() else note_text
        else:
            out.insert(0, {"type": "text", "text": note_text})
    return out


class SessionImageBudget:
    """The layer bound to one runtime session: applies the budget and keeps its ledger.

    Owned by the object that sends ``session/prompt`` for a session (the ACP
    session handle, the direct client). *session_key* and *session_id* are read
    on every call: a pooled handle is rebound to its owning session on claim,
    and the direct client's native sid changes on a reset. The durable ledger
    is used whenever the session has a durable record and describes this sid;
    otherwise the ledger lives here, for as long as the owner does.

    The ledger moves in two steps. :meth:`apply` judges the blocks and STAGES the
    recomputed ledger; :meth:`commit` records it once the prompt has actually
    been written to the runtime, and :meth:`discard` drops it when the write
    never happened. Charging at build time instead would record an image the
    conversation never received: a runtime that dies between the build and the
    write makes the caller re-queue the same message, and the retry would then
    read the undelivered image as "sent earlier" and drop it.
    """

    def __init__(self, session_key: Callable[[], str], session_id: Callable[[], str]) -> None:
        self._session_key = session_key
        self._session_id = session_id
        self._local: dict[str, Any] = empty_ledger()
        # ``(session key, ledger is durable, ledger)`` staged by ``apply`` for the
        # prompt being built; ``None`` when nothing is owed.
        self._pending: tuple[str, bool, dict[str, Any]] | None = None

    async def apply(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """*blocks* with this session's dedup and budget applied; stages the ledger.

        A list without image blocks is returned as is, without touching the
        ledger. Hashing is offloaded: a prompt can carry several multi-megabyte
        payloads, and digesting them on the event loop would pause every other
        session's streaming for the duration. A stage left over from a build
        that was never written is replaced, not accumulated.
        """
        self._pending = None
        if not has_image_blocks(blocks):
            return blocks
        key = self._session_key() or ""
        sid = self._session_id() or ""
        durable = load_image_ledger(key, sid)
        if durable is None and self._local["sid"] != sid:
            # The in-memory ledger describes the conversation it was built in;
            # a new native conversation on this owner starts from nothing.
            self._local = empty_ledger(sid)
        ledger = durable if durable is not None else self._local
        result = await asyncio.to_thread(apply_image_budget, blocks, ledger)
        if result.ledger != ledger:
            self._pending = (key, durable is not None, result.ledger)
        if result.sent_earlier or result.over_budget or result.evicted:
            # Content-free counts only, like the structure summary logged
            # beside it: never a name, a path or a byte of an image.
            logger.info(
                "acp prompt: %d image block(s) inlined, %d dropped as sent earlier, "
                "%d dropped as over the image budget, %d ledger digest(s) evicted",
                result.inlined,
                result.sent_earlier,
                result.over_budget,
                result.evicted,
            )
        return result.blocks

    def commit(self) -> None:
        """Record the staged ledger: the prompt it describes reached the runtime.

        Called right after the ``session/prompt`` write succeeds. A no-op when
        nothing was staged (a text-only prompt, a command turn).
        """
        pending, self._pending = self._pending, None
        if pending is None:
            return
        key, durable, ledger = pending
        if durable:
            # SessionMap's on-loop mutation marks the map dirty and defers the
            # file write to its worker thread (its own threading contract);
            # nothing here waits on disk.
            store_image_ledger(key, ledger)
        else:
            self._local = ledger

    def discard(self) -> None:
        """Drop the staged ledger: the prompt it describes was never written."""
        self._pending = None

    def reset(self) -> None:
        """Forget every inlined image: the native conversation was emptied.

        A confirmed native clear keeps the session's ``sid`` while dropping its
        whole history, so nothing the ledger names is in the conversation any
        more and a picture attached again must be inlined again. Clears the
        durable record when the session has one, the in-memory ledger otherwise,
        and any stage in flight.
        """
        self._pending = None
        sid = self._session_id() or ""
        self._local = empty_ledger(sid)
        key = self._session_key() or ""
        if load_image_ledger(key, sid) is not None:
            store_image_ledger(key, empty_ledger(sid))
