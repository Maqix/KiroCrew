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
a gateway restart, and it is scoped to the native conversation: a session whose
``sid`` changes (``/new``, a discarded conversation, a provider switch) starts
an empty ledger, because the new conversation carries none of the old images. A
session with no durable record (a stateless cron or subagent session, the direct
client) keeps an in-memory ledger for the life of its handle.

A LEAF module, like :mod:`kiro_crew.imaging`: it imports nothing from
``kiro_crew.acp`` (which imports it) and nothing from ``kiro_crew.session_map``
(which implements :class:`ImageLedgerStore`), so both sides can import it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
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

#: Length of a hex SHA-256 digest -- the only shape a retained digest may have.
_DIGEST_HEX_LEN = 64

#: Host-side annotation a producer attaches to an image block:
#: ``{"name": <file name>, "path": <the path as it appeared in the text>}``.
#: Read here to rewrite the block's text marker when the block is degraded, and
#: stripped -- with every other ``_``-prefixed key -- before the list is
#: returned, so it never reaches the wire. A block without it is still deduped
#: and budgeted; only its text note is generic.
IMAGE_BLOCK_SOURCE_KEY = "_source"

#: Marker written where ``[image: <name>]`` stood when the payload was already
#: inlined earlier in this session. Carries no path on purpose: the picture IS
#: in the conversation the model sees, so nothing needs opening.
SENT_EARLIER_MARKER = "[image: {name}, sent earlier]"

#: Marker written where ``[image: <name>]`` stood when the block would cross a
#: budget. The path comes back so a tool-capable agent can still open the file
#: -- the same fallback the builder uses for an image it cannot inline.
OVER_BUDGET_MARKER = "[image: {name}, not inlined: over the image budget; file: {path}]"

#: The over-budget marker for a block whose annotation carries no path.
OVER_BUDGET_MARKER_NO_PATH = "[image: {name}, not inlined: over the image budget]"

#: Notes appended to the text for a degraded block that carries no name, so the
#: model is told an image was dropped even when no marker can be rewritten.
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


def empty_ledger() -> dict[str, Any]:
    """A ledger that has inlined nothing."""
    return {"hashes": [], "b64_bytes": 0}


def normalize_ledger(raw: object) -> dict[str, Any]:
    """*raw* as a well-formed ledger, dropping anything a ledger cannot hold.

    Applied at every point of retention -- when a ledger is read back from the
    session record and again before one is stored -- so a hand-edited or
    corrupt record can neither grow the list past :data:`MAX_LEDGER_HASHES`
    nor retain a string that is not a digest. Anything malformed reads as an
    empty ledger, which only ever costs a re-inline.
    """
    if not isinstance(raw, dict):
        return empty_ledger()
    hashes_raw = raw.get("hashes")
    hashes = (
        [h for h in hashes_raw if isinstance(h, str) and len(h) == _DIGEST_HEX_LEN]
        if isinstance(hashes_raw, list)
        else []
    )
    b64_raw = raw.get("b64_bytes")
    b64_bytes = b64_raw if isinstance(b64_raw, int) and not isinstance(b64_raw, bool) else 0
    return {"hashes": hashes[-MAX_LEDGER_HASHES:], "b64_bytes": max(0, b64_bytes)}


def load_image_ledger(session_key: str) -> dict[str, Any] | None:
    """The durable ledger for *session_key*, or ``None`` when it has no durable record.

    ``None`` is the signal to fall back to an in-memory ledger; an empty dict is
    a durable record that has inlined nothing yet.
    """
    store = _STORE
    if not session_key or store is None:
        return None
    raw = store.get_image_ledger(session_key)
    return None if raw is None else normalize_ledger(raw)


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
    blocks: list[dict[str, Any]],
    ledger: dict[str, Any] | None,
    *,
    max_prompt_images: int = MAX_PROMPT_IMAGE_BLOCKS,
    max_prompt_b64_bytes: int = MAX_PROMPT_IMAGE_B64_BYTES,
    max_session_b64_bytes: int = MAX_SESSION_IMAGE_B64_BYTES,
) -> ImageBudgetResult:
    """Dedup and budget the image blocks in *blocks* against *ledger*.

    A pure function over the finished block list and the session's ledger, so it
    composes with any producer: it reads only ``type``, ``data`` and the
    optional :data:`IMAGE_BLOCK_SOURCE_KEY` annotation. Blocks are judged in
    order. An image whose digest the ledger already holds -- or that an earlier
    block of this same prompt already inlined -- is dropped and its marker
    rewritten to :data:`SENT_EARLIER_MARKER`. Otherwise the block is kept only
    while the prompt stays within *max_prompt_images* and *max_prompt_b64_bytes*
    and the session total stays within *max_session_b64_bytes*; a block that
    would cross any of them is dropped and its marker rewritten to
    :data:`OVER_BUDGET_MARKER`. Only KEPT blocks enter the ledger: a block the
    budget refused was never sent, so a later prompt may still inline it.

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
            inlined + 1 > max_prompt_images
            or prompt_bytes + size > max_prompt_b64_bytes
            or session_bytes + size > max_session_b64_bytes
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
        ledger={"hashes": hashes[-MAX_LEDGER_HASHES:], "b64_bytes": session_bytes},
        inlined=inlined,
        sent_earlier=sent_earlier,
        over_budget=over_budget,
        evicted=evicted,
    )


def _degraded_marker(annotation: dict[str, Any], reason: str) -> tuple[str | None, str]:
    """``(marker to find, replacement)`` for a degraded block; the marker is None when unnamed."""
    name = annotation.get("name")
    if not isinstance(name, str) or not name:
        return None, (
            UNNAMED_SENT_EARLIER_NOTE
            if reason == _REASON_SENT_EARLIER
            else UNNAMED_OVER_BUDGET_NOTE
        )
    if reason == _REASON_SENT_EARLIER:
        return f"[image: {name}]", SENT_EARLIER_MARKER.format(name=name)
    path = annotation.get("path")
    if isinstance(path, str) and path:
        return f"[image: {name}]", OVER_BUDGET_MARKER.format(name=name, path=path)
    return f"[image: {name}]", OVER_BUDGET_MARKER_NO_PATH.format(name=name)


def _rewrite_markers(
    blocks: list[dict[str, Any]], degraded: list[tuple[dict[str, Any], str]]
) -> list[dict[str, Any]]:
    """Rewrite each degraded image's ``[image: <name>]`` marker in the text blocks.

    The first occurrence of the marker across the text blocks, in order, is
    replaced; a degraded block whose annotation carries no name, or whose marker
    is not in the text, is reported with a generic note appended to the last
    text block (or a new text block when there is none). Text blocks are copied
    before they are edited, so the caller's list is never mutated.
    """
    out = [dict(b) if _is_text_block(b) else b for b in blocks]
    text_indexes = [i for i, b in enumerate(out) if _is_text_block(b)]
    notes: list[str] = []
    for annotation, reason in degraded:
        marker, replacement = _degraded_marker(annotation, reason)
        placed = False
        if marker is not None:
            for i in text_indexes:
                text = out[i]["text"]
                at = text.find(marker)
                if at >= 0:
                    out[i]["text"] = text[:at] + replacement + text[at + len(marker) :]
                    placed = True
                    break
        if not placed:
            notes.append(
                UNNAMED_SENT_EARLIER_NOTE
                if reason == _REASON_SENT_EARLIER
                else UNNAMED_OVER_BUDGET_NOTE
            )
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
    session handle, the direct client). *session_key* is read on every call
    because a pooled handle is rebound to its owning session on claim. The
    durable ledger is used whenever the session has a durable record; otherwise
    the ledger lives here, for as long as the owner does.
    """

    def __init__(self, session_key: Callable[[], str]) -> None:
        self._session_key = session_key
        self._local: dict[str, Any] = empty_ledger()

    async def apply(self, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """*blocks* with this session's dedup and budget applied.

        A list without image blocks is returned as is, without touching the
        ledger. Hashing is offloaded: a prompt can carry several multi-megabyte
        payloads, and digesting them on the event loop would pause every other
        session's streaming for the duration.
        """
        if not has_image_blocks(blocks):
            return blocks
        key = self._session_key() or ""
        durable = load_image_ledger(key)
        ledger = durable if durable is not None else self._local
        result = await asyncio.to_thread(apply_image_budget, blocks, ledger)
        if result.ledger != ledger:
            if durable is not None:
                # SessionMap's on-loop mutation marks the map dirty and defers
                # the file write to its worker thread (its own threading
                # contract); nothing here waits on disk.
                store_image_ledger(key, result.ledger)
            else:
                self._local = result.ledger
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
