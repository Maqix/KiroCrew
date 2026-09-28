"""A scheduled-job card's preview run: its approvals on the card, and an honest verdict.

``setup_flow._preview_cron`` runs the card's job once through the cron service.
A cron run's tool call that needs a person is a BACKGROUND approval: it names no
slot (an unattended job is not a chat, so no chat's trust may speak for it) and
is denied when nobody answers within the short unattended window. Left to the
approvals inbox alone, a preview whose job needs ``git log`` waits out every
request and then reads as a success over a reply saying the commands were
refused.

Two things prevent that. Neither changes what an approval is, who may answer it or
how it is answered:

* While a preview runs, :func:`pending_for_card` lists the pending approvals
  whose ``run_session`` (written by the gateway as provenance only) is that
  job's cron session, for ``GET /api/setup/cards/{id}/approvals``. The card
  answers them through the same ``POST /api/approvals/{id}/{action}`` the inbox
  uses.
* :class:`ApprovalWatch` records how each of those approvals ended, and
  :func:`preview_outcome` reports a run that needed an approval nobody gave as a
  ``failure``.

Which job a card's preview is running is held in this process's memory for the
length of the run, not read from the card store: the store is writable from the
agent's sandbox, and the card must show only the approvals of the run the owner
started.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from kiro_crew.cron import cron_job_id_from_session_key

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState

logger = logging.getLogger(__name__)

#: How often a running preview looks for a new approval. A person has to see a
#: request before answering it, so one cannot be raised and answered unseen
#: inside this interval.
_WATCH_POLL_SECS = 0.25

#: ``outcome.preview.reason`` when the run needed an approval nobody gave.
REASON_APPROVAL_NOT_GIVEN = "approval_not_given"

#: Card id -> the watch of its running preview.
_active: dict[str, "ApprovalWatch"] = {}


def _owned_by(record: dict[str, Any], job_id: str) -> bool:
    return bool(job_id) and cron_job_id_from_session_key(record.get("run_session")) == job_id


def _pending_for_job(state: "DashboardState", job_id: str) -> list[dict[str, Any]]:
    records = getattr(state, "_pending_approvals", None) or {}
    futures = getattr(state, "_approval_futures", None) or {}
    pending: list[dict[str, Any]] = []
    for approval_id, record in list(records.items()):
        future = futures.get(approval_id)
        if not _owned_by(record, job_id) or future is None or future.done():
            continue
        # The record's text fields were redacted when it was registered.
        pending.append(
            {
                "id": str(record.get("id") or approval_id),
                "tool": str(record.get("tool") or ""),
                "tool_input": str(record.get("tool_input") or ""),
                "tool_purpose": str(record.get("tool_purpose") or ""),
                "ts": float(record.get("ts") or 0),
            }
        )
    pending.sort(key=lambda r: float(r["ts"]))
    return pending


def pending_for_card(state: "DashboardState", card_id: str) -> list[dict[str, Any]]:
    """The approvals *card_id*'s running preview waits on, oldest first.

    Empty when no preview of that card is running in this process. Listing one
    also hands it to the watch, so an approval answered on the card is counted
    however soon after the watch's last look the answer came.
    """
    watch = _active.get(card_id)
    if watch is None:
        return []
    watch.scan()
    return _pending_for_job(state, watch.job_id)


def _unattended_wait_secs(state: "DashboardState") -> int:
    secs = getattr(state, "_BACKGROUND_APPROVAL_TIMEOUT_SECS", None)
    if not isinstance(secs, (int, float)):
        from kiro_crew.dashboard.state import DashboardState

        secs = DashboardState._BACKGROUND_APPROVAL_TIMEOUT_SECS
    return int(secs)


@dataclass
class ApprovalTally:
    """How the approvals one preview run raised ended."""

    allowed: int = 0
    rejected: int = 0
    #: Expired, or cancelled with the run, before anyone answered.
    unanswered: int = 0

    @property
    def asked(self) -> int:
        return self.allowed + self.rejected + self.unanswered

    @property
    def not_given(self) -> int:
        return self.rejected + self.unanswered


class ApprovalWatch:
    """Follow one card's preview run: expose its approvals, then count their ends."""

    def __init__(self, state: "DashboardState", card_id: str, job_id: str) -> None:
        self._state = state
        self.card_id = card_id
        self.job_id = job_id
        self._futures: dict[str, asyncio.Future[Any]] = {}
        self._task: asyncio.Task[None] | None = None

    def start(self) -> "ApprovalWatch":
        _active[self.card_id] = self
        self._task = asyncio.create_task(self._follow())
        return self

    async def _follow(self) -> None:
        while True:
            self.scan()
            await asyncio.sleep(_WATCH_POLL_SECS)

    def scan(self) -> None:
        # The coordinator pops a record once its wait ends, so each future is
        # taken while the request is still open; its end state is read in stop().
        records = getattr(self._state, "_pending_approvals", None) or {}
        futures = getattr(self._state, "_approval_futures", None) or {}
        for approval_id, record in list(records.items()):
            if approval_id in self._futures or not _owned_by(record, self.job_id):
                continue
            future = futures.get(approval_id)
            if future is not None:
                self._futures[approval_id] = future

    async def stop(self) -> ApprovalTally:
        if _active.get(self.card_id) is self:
            del _active[self.card_id]
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.scan()
        tally = ApprovalTally()
        for future in self._futures.values():
            # A wait that timed out, or was cancelled with the run, cancels the
            # future; a decision sets it to the approved flag.
            if future.cancelled() or not future.done() or future.exception() is not None:
                tally.unanswered += 1
            elif future.result() is True:
                tally.allowed += 1
            else:
                tally.rejected += 1
        return tally


def preview_outcome(
    state: "DashboardState", status: str, text: str, tally: ApprovalTally
) -> dict[str, Any]:
    """The card's ``outcome.preview``, honest about the run's approvals.

    A run whose approval was rejected or went unanswered did not do what the job
    asks, whatever its reply says, and the cron service still records it ``ok``:
    only a security block counts against a run there, because an absent approver
    says nothing about the job. So a ``success`` with any approval not given is
    reported as a ``failure`` with :data:`REASON_APPROVAL_NOT_GIVEN`. A run that
    already failed or timed out keeps its own status. ``approvals`` is present
    once the run asked at all, so the card can say the job will ask again on every
    run, and how long an unanswered request waits.
    """
    preview: dict[str, Any] = {"status": status, "text": text}
    if tally.asked:
        preview["approvals"] = {
            "asked": tally.asked,
            "allowed": tally.allowed,
            "rejected": tally.rejected,
            "unanswered": tally.unanswered,
            "wait_secs": _unattended_wait_secs(state),
        }
        if tally.not_given and status == "success":
            preview["status"] = "failure"
            preview["reason"] = REASON_APPROVAL_NOT_GIVEN
    return preview
