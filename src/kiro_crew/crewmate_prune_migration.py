"""One-time startup migration: prune the crewmates the retired agent sync left.

An enrol-on-mount build of the dashboard called ``POST /api/agents/sync`` on
every chat mount, and that sync enrolled discovered specs as crewmates: a
``config.agents`` row with no ``member_id``, on the shared ``default`` memory
store, bound to the spec by name and stamped with the spec's discovery source
-- ``builtin``, ``package``, or ``aim`` (the package source's older name). An
existing install therefore carries one crewmate per synced agent -- user
specs, package specs, the runtime's own helper specs, skill-view alias files --
most of them never opened, and later edits (a model, a star, an avatar) have
been made to some of them without anyone choosing the crewmate itself.

This module runs once at gateway startup and settles those rows by one rule.
A non-default ``config.agents`` row is **removal-eligible** only when its
``source`` is exactly one of the retired sync's stamps (:data:`SYNC_ROW_SOURCES`:
``builtin``, ``package``, ``aim``), or is ``kirocrew`` with a ``kiro_agent``
that is one of the three core runtime specs discovery stamps ``kirocrew``
(:data:`CORE_RUNTIME_AGENT_NAMES`). Every other row is KEPT without further
judgement: a ``kirocrew`` row bound to anything else (a crewmate the dashboard
created before ``member_id`` existed), and a row carrying any other ``source``
at all -- an app's own stamp (apps create crewmates themselves, and the roster
hides those rows rather than this pass deleting them), any other string, or a
value that is not a string. A row with no ``source`` key reads as the loader
reads it, ``kirocrew`` (the record's default), so it is eligible only when
bound to a core runtime spec.

A removal-eligible row is still KEPT when ANY of these holds:

* it has a non-empty ``member_id`` -- a crewmate the owner created;
* its Crewmates-page DM thread holds a turn -- the owner chatted with it
  (:func:`_chatted`: the live transcript or an archived segment has a row past
  the metadata line);
* ``config.local.json`` names it, or a team lists it (below);
* it is the default row (``default`` or ``cfg.default_agent``).

EVERY OTHER ELIGIBLE ROW IS REMOVED (:func:`remove_never_chatted`), whatever
its shape or binding: rows bound to the runtime's own helper specs (conductor,
worker, heartbeat and the rest), crew private copies, rows whose spec is not
installed, rows bound to a skill-view alias, rows whose name is not their
``kiro_agent``, ``kirocrew`` rows bound to a core runtime spec, and rows whose
other fields were edited -- a model, effort, avatar, colour, triggers, star,
workspace or store is not a claim on the crewmate; only ``member_id``, the
overlay, a team and a chat are. Only the ``config.json`` row goes: the agent
stays installed, its spec under ``~/.kiro/agents`` is never touched, and any
transcript stays.

The rails that stay are about doubt and concurrency, not category:

* **The row is judged as ``config.json`` holds it.** ``member_id``,
  ``source`` and ``kiro_agent`` are read off the RAW row (a key the row lacks
  reads as the loader's default, ``kirocrew`` for ``source``), so the same
  test runs at the scan and again inside the delete's lock
  (:func:`_is_owners_row`). Both config layers are consulted: a name that
  ``config.local.json`` touches in its own ``agents`` section --
  ``kirocrew config set --local agents.<name>.model``, the capability
  writer's overlay binding -- is the owner's and is kept, because deleting the
  base row would leave the overlay leaf as a crewmate bound to nothing. So is
  a crewmate that any team lists (``crew_teams.read_teams``): placing it on a
  team is the owner's own act. A team document that is there but cannot be
  read keeps every candidate (listed under ``doubted``), since none of them
  can be shown to be off a team.
* **Chatted means the DM thread holds a turn.** The one piece of evidence is
  the crewmate's own Crewmates-page thread: its transcript, live or an
  archived segment, has a row past the metadata line (:func:`_chatted`). A
  session elsewhere that ran the same agent -- a subagent, a cron job, an
  app's own slot, a plain chat -- used the AGENT, not the crewmate, and does
  not keep the row. Opening the thread writes a DM binding and at most a
  metadata line, so a crewmate that was only clicked in the roster is not
  chatted. The binding and the transcript are read STRICTLY: one that is
  there but cannot be read or judged keeps that crewmate (listed under
  ``doubted``), and no conversation log at all keeps every candidate. The
  pass always completes and writes the marker; it never loops boot after boot
  on one bad file, and a row it keeps loses nothing by staying.
* **Agent-writable paths are opened defensively.** The DM transcripts are
  opened with ``open_file_no_reparse`` (``O_NOFOLLOW`` / reparse-point refusal
  settled in the same operation as the open, ``O_NONBLOCK`` so a FIFO cannot
  hang the pass) and read only when ``fstat`` says regular file -- a link, a
  FIFO or anything else is not a transcript and is "no record", neither
  evidence nor doubt.
* **One process runs the pass.** The whole pass -- the marker check, the
  history scan, every delete and the marker write -- runs under an exclusive
  cross-process lock on :data:`PRUNE_LOCK` beside the marker
  (``platform_compat.file_lock``), and the marker itself is created
  ``O_EXCL``. A second gateway on the same data home therefore cannot run its
  own pass beside this one: its pass waits on the lock -- and its own request
  barrier holds its writers for as long as it waits -- then finds the marker
  and returns. The wait has no give-up: each :data:`PRUNE_LOCK_WAIT_S` that
  passes logs one WARNING and tries again, because a contender that returned
  while the holder still held the lock would settle its own barrier and let a
  session bind a crewmate the holder had not judged yet, and the holder would
  then delete a row that is in use. A holder that dies releases the lock
  with its process, so the wait ends. A process-local event alone would let
  that second process bind a session to a candidate between this process's
  history scan and its delete.
* **Serialized against every session-agent writer, off the boot path.** The
  gateway clears ``DashboardState.crewmate_prune_settled`` BEFORE the listener
  binds (``_register_crewmate_prune_gate``); the pass itself is kicked as a
  tracked background task right after the bind (``_kick_crewmate_prune``) and
  sets the event in ``finally``. Nothing on the startup path awaits it, so
  readiness is never gated by a scan whose cost scales with the session
  count; the slot restores need not wait either: a removed row's DM thread
  held no turn, and a session that ran its agent elsewhere resolves the same
  name onto the installed agent on the default crew's workspace and memory --
  the binding the removed row carried -- so no restore depends on the row.
  That function's middleware holds every mutating request on it -- the chat
  send, slot create, slot agent switch, member thread, channel and import
  routes under ``/api/`` and the OpenAI-compatible ``POST
  /v1/chat/completions`` are all such requests -- so no session can bind an
  agent, no DM binding can appear and no ``member_id`` can be stamped between
  a candidate's check and its delete. It also holds every request under
  ``/api/members`` whatever its method, so the roster is read after the pass
  has settled rather than beside a delete. The writers that do not come
  through HTTP -- the subagent pump, channel agent resume, cron dispatch --
  start only after ``await_crewmate_prune_settled`` returns (in
  ``GatewayOrchestrator.run`` after the memory barrier, past
  ``KIROCREW_READY``; in the standalone dashboard before its inline channel
  resume), and that helper returns only once the pass has RETURNED: when the
  pass outlives its budget the helper sets the abandon flag -- read here
  before each candidate and again inside the config lock before each delete
  -- and the pass keeps whatever it has not judged (listed under ``doubted``),
  writes its marker and returns; no writer ever runs beside a pass that can
  still delete. Each candidate's check runs immediately before its own
  removal, never once for the whole list.
* **A refused delete is not a commit.** The delete re-tests the KEEP
  conditions inside the base config lock, on the row as the file holds it
  then: ``member_id`` must still be empty, the row must still be
  removal-eligible by its ``source`` and ``kiro_agent`` (:func:`_is_owners_row`),
  the name must still not be the
  default row, the overlay must still not name it -- read under its own
  sidecar lock, taken inside the base lock and held until the base write has
  committed, so no overlay leaf can land for the name between that check and
  the delete -- and no team may list it, the team document re-read under
  ``crew_teams.document_lock``, taken innermost and held the same way, so no
  team write can place the name between that check and the delete. No spec is
  read: nothing about the bound spec decides the rule, so ``agents_spec_lock``
  is not taken -- the team lock's own contract is the registry's lock first,
  then it, and a lock this pass never holds cannot invert any order. A row
  that changed meanwhile in any of these ways -- a member-aware write stamped
  ``member_id``, an app restamped it with its own ``source``, a rebinding made
  it a kept ``kirocrew`` row, an overlay leaf appeared, a team lists it -- is
  refused, the pass writes no marker and logs which rows, and the next boot
  re-judges them.
* **Agent files are never touched.** Only ``config.json`` rows move. The specs
  under ``~/.kiro/agents`` are never read or written by this pass, and any
  transcript on disk stays exactly as it is.
* **Idempotent, marker-gated.** A completed pass (even a no-op) writes
  :data:`PRUNE_MARKER` under the config directory with what it did -- the same
  marker-file seam the config loader's own one-shot migrations use
  (``CONNECTIONS_UI_MIGRATION_MARKER``); the next boot finds the marker and
  returns at once. It runs from ``start_dashboard`` rather than inside the
  loader because the decision needs chat history, which only the running
  gateway has. Removed rows do not come back: nothing in the dashboard calls
  ``POST /api/agents/sync`` (``useAgents`` reads the catalog), so the rows this
  pass removes come only from installs that ran an enrol-on-mount build, and
  from the edits made to those rows since.
"""

from __future__ import annotations

import contextlib
import dataclasses
import errno
import json
import logging
import os
import stat
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from kiro_crew.agent_files import AGENT_FILENAME, GUEST_AGENT_FILENAME, LITE_AGENT_FILENAME
from kiro_crew.config.loader import (
    ConfigReadError,
    KiroCrewAgentConfig,
    KiroCrewConfig,
    _config_write_lock,
    _lock_target,
    coerce_dict_section,
    config_local_path,
    read_config_for_update,
    update_config_locked,
)
from kiro_crew.config.paths import config_dir
from kiro_crew.crew_teams import TeamsUnreadable, document_lock, read_teams
from kiro_crew.jsonl_util import OversizedRecord, UnreadableRecord, strict_raw_records
from kiro_crew.platform_compat import file_lock, open_file_no_reparse, open_lock_file

logger = logging.getLogger(__name__)

#: Written under the config directory once a pass completes. Its body is the
#: record of what the pass did, so an operator can see which crewmates left
#: and which were kept because their history could not be read. Earlier
#: markers (``crewmate_prune_migrated.json``, ``crewmate_prune_v2_migrated.json``,
#: ``crewmate_prune_v3_migrated.json``) are left in place: the passes that wrote
#: them judged a narrower set of rows, so a new marker name lets the current
#: pass run once on those installs too.
PRUNE_MARKER = "crewmate_prune_v4_migrated.json"

#: The cross-process lock the whole pass runs under, beside the marker. Two
#: gateway processes on one data home take turns here; the second finds the
#: first's marker and returns.
PRUNE_LOCK = "crewmate_prune.lock"

#: How long one acquire of :data:`PRUNE_LOCK` waits before the pass logs that
#: it is still waiting and tries again. Not a give-up: a pass never returns
#: while another process holds the lock (see the module docstring), it only
#: says so this often. Longer than the gateway's own writer budget for the
#: pass (60 s), so the first line is not written for a holder that is merely
#: slow.
PRUNE_LOCK_WAIT_S = 120.0

#: The ``source`` the loader gives a ``config.agents`` row that carries no
#: ``source`` key (the record's default), and the stamp the dashboard's own
#: crewmate writers leave. A row stamped this way is removal-eligible only when
#: bound to a core runtime spec; bound to anything else it is a crewmate the
#: dashboard created, and is kept.
OWNER_ROW_SOURCE = KiroCrewAgentConfig().source

#: The ``source`` stamps the retired agent sync wrote: it copied the bound
#: spec's discovery source onto the row -- ``builtin`` for a spec the user wrote
#: and ``package`` for one a package installed (``aim`` is that source's older
#: name). Matched exactly: any other value, an app's own stamp included, is not
#: the sync's row and is kept.
SYNC_ROW_SOURCES = frozenset({"builtin", "package", "aim"})

#: The agent names of the three core runtime specs discovery stamps
#: ``kirocrew``: ``agent_discovery._global_agent_info`` derives a global spec's
#: name from its ``name`` field, falling back to the filename stem, and the
#: writers in ``agent.py`` give these three files exactly their stems as names
#: (``kirocrew``, ``kirocrew-lite``, ``kirocrew-guest``). A ``kirocrew``-stamped
#: row bound to one of them is not a crewmate anyone created -- the default row
#: is the crewmate on the main spec -- and is removed like any other leftover.
CORE_RUNTIME_AGENT_NAMES = frozenset(
    Path(filename).stem for filename in (AGENT_FILENAME, LITE_AGENT_FILENAME, GUEST_AGENT_FILENAME)
)


class HistoryUnreadable(RuntimeError):
    """A piece of chat history could not be read.

    Raised by the strict readers below and caught by :func:`prune_synced_crewmates`,
    which keeps the crewmates the unreadable piece could have vouched for.
    """


@dataclasses.dataclass
class PruneReport:
    removed: list[str] = dataclasses.field(default_factory=list)
    kept: list[str] = dataclasses.field(default_factory=list)
    #: Kept because history could not be read in full; reason per name.
    doubted: dict[str, str] = dataclasses.field(default_factory=dict)
    refused: list[str] = dataclasses.field(default_factory=list)
    skipped_marker: bool = False
    #: How many :data:`PRUNE_LOCK_WAIT_S` waits passed with another process
    #: holding :data:`PRUNE_LOCK` before this pass got it (or found the marker).
    lock_waits: int = 0


def marker_path() -> Path:
    return config_dir() / PRUNE_MARKER


def lock_path() -> Path:
    return config_dir() / PRUNE_LOCK


def _raw_agents_section(path: Path | None = None) -> dict:
    """The ``agents`` section exactly as one config file holds it.

    ``path`` defaults to ``config.json``; pass :func:`config_local_path` for the
    overlay. A file that is absent reads as an empty section; one that is
    present but unreadable raises ``ConfigReadError`` (fail closed: the pass
    cannot judge a layer it cannot see).
    """
    doc = read_config_for_update(path)
    agents = doc.get("agents") if isinstance(doc, dict) else None
    return agents if isinstance(agents, dict) else {}


def _is_owners_row(raw: dict) -> bool:
    """Whether a RAW ``config.agents`` row is kept by its own fields alone.

    A row is removal-eligible only when its ``source`` is exactly one of
    :data:`SYNC_ROW_SOURCES`, or is :data:`OWNER_ROW_SOURCE` with a
    ``kiro_agent`` in :data:`CORE_RUNTIME_AGENT_NAMES`; every other ``source``
    -- ``kirocrew`` bound to any other agent, an app's own stamp, any other
    string, a value that is not a string -- keeps the row. An eligible row is
    still kept when it has a non-empty ``member_id`` (a created crewmate); any
    truthy value keeps, since a ``member_id`` that is not even a string is
    doubt about a created crewmate, and doubt keeps.

    Read on the row as ``config.json`` holds it: a key the row lacks reads as
    the loader reads it, so a row with no ``source`` key is a ``kirocrew`` row
    and a row with no ``member_id`` key has none. The chat, overlay and team
    tests are the caller's; this is the part of the rule that the row alone
    answers, at the scan and again under the lock.
    """
    if raw.get("member_id"):
        return True
    source = raw.get("source", OWNER_ROW_SOURCE)
    if not isinstance(source, str):
        return True
    if source in SYNC_ROW_SOURCES:
        return False
    if source == OWNER_ROW_SOURCE:
        return raw.get("kiro_agent") not in CORE_RUNTIME_AGENT_NAMES
    return True


def _teamed_names() -> frozenset[str]:
    """Every crewmate name some team lists, as ``crew-teams/teams.json`` holds it.

    An absent document is no teams. One that is there but cannot be read raises
    :class:`~kiro_crew.crew_teams.TeamsUnreadable` (``read_teams`` never answers
    an empty list for it); the caller keeps every candidate on that doubt. The
    names are taken as written, not filtered against the registry: a candidate
    is a registry name, so a stale entry for a gone crew names nothing here.
    """
    return frozenset(member for team in read_teams() for member in team.members)


def _removal_candidates(
    cfg: KiroCrewConfig, raw_agents: dict, overlay_agents: dict, teamed: frozenset[str]
) -> list[str]:
    """The rows the rule does not keep on their own fields, in config order.

    Every non-default row is a candidate unless :func:`_is_owners_row` keeps it,
    ``overlay_agents`` (the ``agents`` section of ``config.local.json``)
    mentions its name -- whatever the leaf says -- or ``teamed`` (every name
    some team lists, :func:`_teamed_names`) holds it. ``raw_agents`` is the
    ``agents`` section as ``config.json`` holds it, not the default-filled
    dataclasses: the row test must see the row the file holds, and the same
    test is re-run inside the delete's lock. A name whose raw row is not a
    record is left alone; the loader owns what to make of it. Whether a
    candidate is kept or removed is then the chat test's to decide.
    """
    out: list[str] = []
    for name in cfg.agents:
        if name in ("default", cfg.default_agent):
            continue
        if name in overlay_agents or name in teamed:
            continue
        raw = raw_agents.get(name)
        if not isinstance(raw, dict) or _is_owners_row(raw):
            continue
        out.append(name)
    return out


#: Longest first line the DM transcript read accepts. The first line is the
#: metadata record, a few hundred bytes in practice; the cap bounds the cost of
#: one read on an agent-writable tree.
_TRANSCRIPT_META_LINE_MAX = 64 * 1024


def _transcript_has_a_turn(path: Path, *, missing_is_doubt: bool = False) -> bool:
    """Whether one transcript file holds anything past its metadata line.

    A transcript is born with its metadata record and gains one row per
    message, so a second non-blank line means a turn was written; its content
    is not parsed, since even a torn row proves one was being written. A first
    line that parses to something other than the metadata record is a message
    row from an older build, and counts the same way.

    ``False`` when the file does not exist (unless ``missing_is_doubt``), is a
    link, a FIFO or anything but a regular file (opened with
    ``open_file_no_reparse``, ``O_NONBLOCK``: the link refusal lands in the
    open, and a FIFO returns at once instead of hanging), or holds only its
    metadata line. Raises :class:`HistoryUnreadable` when the file is there but
    cannot be judged: an open or read failure, an empty file (a torn write), or
    a first line that is over budget, not UTF-8 or not JSON. The caller keeps
    the crewmate. ``missing_is_doubt`` is for an archived segment already seen
    in a directory listing: if it disappears before the open, its evidence is
    unknown rather than absent.
    """
    try:
        fd = open_file_no_reparse(path, nonblocking=True)
    except FileNotFoundError as exc:
        if missing_is_doubt:
            raise HistoryUnreadable(
                f"transcript {path.name} disappeared before it could be opened"
            ) from exc
        return False
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return False
        raise HistoryUnreadable(f"transcript {path.name} could not be opened: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            first = fh.readline(_TRANSCRIPT_META_LINE_MAX + 1)
            if len(first) > _TRANSCRIPT_META_LINE_MAX:
                raise HistoryUnreadable(
                    f"transcript {path.name}: first line exceeds {_TRANSCRIPT_META_LINE_MAX} bytes"
                )
            try:
                text = first.decode("utf-8").strip()
            except UnicodeError as exc:
                raise HistoryUnreadable(f"transcript {path.name} is not UTF-8: {exc}") from exc
            if not text:
                raise HistoryUnreadable(f"transcript {path.name} is empty: no metadata line")
            try:
                record = json.loads(text)
            except ValueError as exc:
                raise HistoryUnreadable(
                    f"transcript {path.name}: first line is not JSON: {exc}"
                ) from exc
            if not isinstance(record, dict) or record.get("_type") != "metadata":
                return True
            try:
                for line in strict_raw_records(fh, path, cap=_TRANSCRIPT_META_LINE_MAX):
                    if line.strip():
                        return True
            except OversizedRecord:
                # A row too long to hold is still a row: a turn was written.
                return True
            except UnreadableRecord as exc:
                raise HistoryUnreadable(f"transcript {path.name} could not be read: {exc}") from exc
            return False
    except OSError as exc:
        raise HistoryUnreadable(f"transcript {path.name} could not be read: {exc}") from exc
    finally:
        if fd >= 0:
            os.close(fd)


def _dm_binding_slot_key(name: str, slug: str) -> str:
    """The slot key this crewmate's DM binding records, or ``""`` for none.

    Read STRICTLY, not through ``read_dm_binding``: that reader is total by
    contract and answers "not bound" for an unreadable file or a malformed
    payload alike, which a removal must never mistake for "never opened". Only
    a binding file that does not exist reads as none; any other failure raises
    :class:`HistoryUnreadable`. A binding that names another crew (slugs
    collide) is not this crewmate's thread and reads as none.
    """
    from kiro_crew import members as members_mod
    from kiro_crew.atomic_write import read_bytes_with_retry

    try:
        path = members_mod.dm_binding_path(slug)
    except Exception as exc:  # noqa: BLE001 -- an unresolvable path is "unknown", never "no"
        # ``MemberSlugError`` included: the slug passed ``member_slug`` already,
        # so here it means the containment check refused a path that resolves
        # outside the trust root -- a binding that may exist, not one that does not.
        raise HistoryUnreadable(f"could not resolve {name!r}'s DM binding: {exc}") from exc
    try:
        raw = read_bytes_with_retry(path)
    except FileNotFoundError:
        return ""
    except Exception as exc:  # noqa: BLE001 -- present but unreadable is "unknown"
        raise HistoryUnreadable(f"could not read {name!r}'s DM binding: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise HistoryUnreadable(f"{name!r}'s DM binding does not parse: {exc}") from exc
    if not isinstance(data, dict):
        raise HistoryUnreadable(f"{name!r}'s DM binding is not a record")
    if data.get("member") != name:
        return ""
    slot_key = data.get("slot_key")
    return slot_key if isinstance(slot_key, str) else ""


def _chatted(cfg: KiroCrewConfig, name: str, sessions_dir: Path) -> bool:
    """Whether the owner ever chatted with this crewmate on the Crewmates page.

    The one piece of evidence is the crewmate's own DM thread holding a turn:
    its transcript, live or an archived segment of it, has a row past the
    metadata line (:func:`_transcript_has_a_turn`). Nothing else counts. A
    session elsewhere that ran as this agent -- a subagent spawn, a cron job,
    an app's own slot, a plain chat that picked the template -- used the
    AGENT, not the crewmate, and the agent stays installed after the row goes.
    Opening the thread without sending anything writes the DM binding and at
    most a metadata line, so a crewmate that was only clicked in the roster is
    not chatted either.

    The thread's transcript lives under ``dashboard_<slot key>``. The slot key
    is derived from the slug (:func:`~kiro_crew.members.member_slot_key`), and
    the DM binding's recorded key is read too, so a thread opened under another
    derivation is not missed. A binding that cannot be read, or a transcript
    that is there but cannot be judged, raises :class:`HistoryUnreadable` and
    the caller keeps the crewmate.
    """
    from kiro_crew import members as members_mod
    from kiro_crew.history import ARCHIVE_DIR_NAME, ARCHIVE_SEGMENT_DELIMITER, _safe_key

    try:
        slug = members_mod.member_slug(name, cfg)
    except members_mod.MemberSlugError:
        # No slug means no DM thread can exist.
        return False
    slot_keys = {members_mod.member_slot_key(slug)}
    bound = _dm_binding_slot_key(name, slug)
    if bound:
        slot_keys.add(bound)
    stems: list[str] = []
    for slot_key in sorted(slot_keys):
        stem = _safe_key(f"dashboard_{slot_key}")
        if _transcript_has_a_turn(sessions_dir / f"{stem}.jsonl"):
            return True
        stems.append(stem)

    archive = sessions_dir / ARCHIVE_DIR_NAME
    prefixes = tuple(f"{stem}{ARCHIVE_SEGMENT_DELIMITER}" for stem in stems)
    try:
        with os.scandir(archive) as entries:
            archive_names = sorted(
                entry.name
                for entry in entries
                if entry.name.endswith(".jsonl") and entry.name.startswith(prefixes)
            )
    except FileNotFoundError:
        archive_names = []
    except OSError as exc:
        raise HistoryUnreadable(f"could not list archived transcripts: {exc}") from exc
    for stem in stems:
        prefix = f"{stem}{ARCHIVE_SEGMENT_DELIMITER}"
        for name in archive_names:
            if name.startswith(prefix) and _transcript_has_a_turn(
                archive / name, missing_is_doubt=True
            ):
                return True
    return False


def _never_abandoned() -> bool:
    return False


def remove_never_chatted(
    cfg: KiroCrewConfig,
    names: Iterable[str],
    *,
    abandoned: Callable[[], bool] = _never_abandoned,
) -> tuple[list[str], list[str], list[str]]:
    """Delete the ``config.agents`` rows named; returns ``(removed, refused, abandoned)``.

    Each delete re-runs the KEEP conditions inside the base config lock, on the
    row as the file holds it then: the name must still not be the default row
    (``default`` or the document's ``default_agent``); :func:`_is_owners_row`
    must still not keep it -- ``member_id`` still empty, ``source`` still one
    of the sync's stamps or ``kirocrew`` on a core spec; ``config.local.json``
    must still
    not name it, read under the overlay's own sidecar lock; and no team may
    list it, the team document re-read under ``crew_teams.document_lock``. The
    two inner locks are taken inside the base lock, overlay first (the order
    every binding writer keeps) and the team document innermost (its own
    contract is the registry's lock first, then it, and nothing takes a
    registry or overlay lock while holding it), and both are held until the
    base write has committed, so neither an overlay writer landing a leaf for
    the name nor a team write placing the name can slip between the check and
    the delete. No spec is read and ``agents_spec_lock`` is not taken: nothing
    about the bound spec is a keep condition, and a lock this pass never holds
    cannot invert any writer's order. A row that changed meanwhile in any of
    these ways -- a member-aware write stamped ``member_id``, an app restamped
    it with its own ``source``, a rebinding made it a kept ``kirocrew`` row,
    the document made it the default, an overlay
    leaf appeared, or a team lists the name -- is newer evidence and is
    refused, not deleted. Nothing but the base row moves: the overlay, the
    spec under ``~/.kiro/agents`` and any transcript stay.

    ``abandoned`` is read inside the config lock, after every re-check and
    right before the delete: once it answers true the row is left in place and
    named in the third list, and so is every row after it. The gateway sets it
    when the pass outlives its budget and then waits for the pass to return
    before any session writer starts, so the delete this check guards is the
    last thing that could still remove a row a new session is about to name.

    Callers hold :data:`PRUNE_LOCK` (:func:`prune_synced_crewmates` does); the
    config lock taken here serializes the row write itself, not the pass.
    """
    removed: list[str] = []
    refused: list[str] = []
    left: list[str] = []
    overlay_path = _lock_target(config_local_path())
    for name in names:
        if abandoned():
            left.append(name)
            continue
        deleted = False
        gave_up = False

        # The overlay's sidecar lock and the team document lock are entered on
        # this stack from inside ``_mutate`` and so outlive the callback: both
        # are released only after ``update_config_locked`` has renamed the base
        # file into place. A lock released when its check returned would leave
        # the window the check exists to close -- an overlay writer landing a
        # leaf for the name, or a team write placing it, after the check and
        # before the base row is gone.
        with contextlib.ExitStack() as locks:

            def _mutate(
                doc: dict,
                _name: str = name,
                _locks: contextlib.ExitStack = locks,
            ) -> dict | None:
                nonlocal deleted, gave_up
                if _name == "default" or _name == doc.get("default_agent"):
                    return None
                agents = coerce_dict_section(doc, "agents")
                raw = agents.get(_name)
                if not isinstance(raw, dict) or _is_owners_row(raw):
                    return None
                # Base lock first, overlay lock second -- the order every
                # binding writer keeps (``_write_bindings``, the template
                # create) -- and the team document lock innermost: its own
                # contract is registry lock first, then it, and nothing takes
                # a registry or overlay lock while holding it, so entering it
                # last cannot invert any order. Both inner locks are entered
                # on the stack that outlives this callback, so they are
                # released only after ``update_config_locked`` has renamed the
                # base file into place: neither an overlay leaf for the name
                # nor a team write placing the name can land between the
                # checks below and the delete. The overlay and the team
                # document are read directly under their locks; nothing is
                # written to either. A lock that cannot be taken or an
                # unreadable overlay or team document is doubt, and doubt
                # refuses.
                try:
                    _locks.enter_context(_config_write_lock(overlay_path))
                    overlay = read_config_for_update(overlay_path)
                    _locks.enter_context(document_lock())
                    teamed = _teamed_names()
                except (OSError, ConfigReadError, TeamsUnreadable):
                    return None
                if _name in teamed:
                    return None
                overlay_agents = overlay.get("agents")
                if isinstance(overlay_agents, dict) and _name in overlay_agents:
                    return None
                # Last check before the delete, under the locks the delete
                # holds: a pass told to stop removes nothing further, whatever
                # it had judged.
                if abandoned():
                    gave_up = True
                    return None
                del agents[_name]
                deleted = True
                return doc

            update_config_locked(mutate=_mutate)
        if deleted:
            removed.append(name)
            del cfg.agents[name]
        elif gave_up:
            left.append(name)
        else:
            refused.append(name)
    return removed, refused, left


def _write_marker(report: PruneReport) -> None:
    """Create the marker ``O_EXCL`` and write the pass's record into it.

    Exclusive creation, not replace: the marker is the claim that ONE pass
    completed, so a marker already there -- another process's, written while
    this one waited on the lock -- is left as it is and ``FileExistsError``
    propagates; the caller reads it as that other pass having settled the
    question. The bytes are flushed to disk before the close, so a marker that
    exists is one whose pass returned.
    """
    body = {
        "migrated_at": time.time(),
        "removed": report.removed,
        "kept": report.kept,
        "doubted": report.doubted,
    }
    marker = marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(os.fspath(marker), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            fh.write(json.dumps(body, indent=2) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    finally:
        if fd >= 0:
            os.close(fd)


#: The ``doubted`` reason for a candidate the pass was told to stop before judging.
ABANDONED_REASON = "the startup barrier timed out before this crewmate was judged"

#: The ``doubted`` reason when ``crew-teams/teams.json`` is there but cannot be
#: read: no candidate can then be shown to be off a team.
TEAMS_UNREADABLE_REASON = "the crew-teams document could not be read; team membership unknown"


def prune_synced_crewmates(
    conversation_log, *, abandoned: Callable[[], bool] = _never_abandoned
) -> PruneReport:
    """Run the pass once. Thread-side; safe to call on every boot.

    ``conversation_log`` is the gateway's :class:`~kiro_crew.history.ConversationLog`;
    ``None`` means history is unavailable, so every candidate is kept on doubt,
    and so does a ``crew-teams/teams.json`` that is there but cannot be read.
    Never raises :class:`HistoryUnreadable` or
    :class:`~kiro_crew.crew_teams.TeamsUnreadable`: unreadable evidence keeps
    the crewmates it could have vouched for, and the pass still finishes.

    The whole pass runs under an exclusive cross-process lock on
    :data:`PRUNE_LOCK`: the marker check, the history scan, each delete and
    the marker write. A second gateway on the same data home waits here --
    its own request barrier holds its writers for the whole wait -- and then
    finds the marker. The wait never gives up: every :data:`PRUNE_LOCK_WAIT_S`
    it logs one WARNING (counted in ``lock_waits``) and tries again, since a
    pass that returned with the lock still held would open its barrier while
    the holder can still delete. A holder's death releases the lock. Between
    tries the marker is checked without the lock: it is written last, under
    the lock, so its presence means every delete is done. Blocking; never
    call on the event loop thread (``file_lock`` refuses to poll there).

    ``abandoned`` is polled before each candidate and inside the config lock
    before each delete (:func:`remove_never_chatted`). Once it answers true
    every candidate not yet removed is kept and listed under ``doubted`` with
    :data:`ABANDONED_REASON`, and the marker is still written: the rows lose
    nothing by staying, and a pass that re-ran every boot would hold the
    gateway's writers every boot. The gateway sets it when the pass outlives
    its startup budget.
    """
    report = PruneReport()
    lock = lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open_lock_file(lock) as lock_fd, contextlib.ExitStack() as held:
        # The acquire is tried on its own so that only IT can read as "another
        # process holds the pass": a failure inside the pass proper propagates
        # as itself, never mislabelled as contention.
        while True:
            try:
                held.enter_context(file_lock(lock_fd, exclusive=True, timeout=PRUNE_LOCK_WAIT_S))
                break
            except OSError as exc:
                # Held by another process. That process owns the pass; this
                # one must not return while it can still delete, because the
                # caller settles its request barrier on return and a session
                # bound here would be invisible to the holder's history scan.
                # So: wait again. The marker is written last, under the lock,
                # so finding it means the holder's deletes are all done.
                report.lock_waits += 1
                if marker_path().exists():
                    report.skipped_marker = True
                    return report
                logger.warning(
                    "crewmate prune still waiting: another process holds %s (%s); "
                    "session writers stay held until it finishes",
                    lock.name,
                    exc,
                )
        return _prune_locked(conversation_log, report, abandoned=abandoned)


def _judge_each(
    cfg: KiroCrewConfig,
    candidates: list[str],
    sessions_dir: Path,
    report: PruneReport,
    *,
    abandoned: Callable[[], bool],
) -> None:
    """Judge and, when never chatted, remove each candidate in turn."""
    # Check and delete ONE candidate at a time: the strict history check
    # runs immediately before its own row's removal, never once for the
    # whole list up front. The gateway holds every mutating request
    # back while the pass runs (``DashboardState.crewmate_prune_settled``,
    # armed before the listener bound), so no session can bind an agent and
    # no thread can be opened between a candidate's check and its delete.
    for name in candidates:
        if abandoned():
            report.doubted[name] = ABANDONED_REASON
            continue
        try:
            chatted = _chatted(cfg, name, sessions_dir)
        except HistoryUnreadable as exc:
            report.doubted[name] = str(exc)
            continue
        if chatted:
            report.kept.append(name)
        else:
            removed, refused, left = remove_never_chatted(cfg, [name], abandoned=abandoned)
            report.removed.extend(removed)
            report.refused.extend(refused)
            for gone in left:
                report.doubted[gone] = ABANDONED_REASON


def _prune_locked(
    conversation_log, report: PruneReport, *, abandoned: Callable[[], bool]
) -> PruneReport:
    """The pass proper; the caller holds :data:`PRUNE_LOCK`."""
    marker = marker_path()
    if marker.exists():
        report.skipped_marker = True
        return report
    cfg = KiroCrewConfig.load()
    raw_agents = _raw_agents_section()
    overlay_agents = _raw_agents_section(config_local_path())
    # An unreadable team document is doubt about EVERY candidate, the way no
    # conversation log is: none can be shown to be off a team. Read as "no
    # teams" only for the candidate test below, so the doubt can name the rows
    # it keeps; nothing is judged or removed on it.
    teams_doubt = ""
    try:
        teamed = _teamed_names()
    except TeamsUnreadable as exc:
        teams_doubt = f"{TEAMS_UNREADABLE_REASON} ({exc})"
        teamed = frozenset()
    candidates = _removal_candidates(cfg, raw_agents, overlay_agents, teamed)
    if candidates:
        sessions_dir = getattr(conversation_log, "_dir", None)
        if teams_doubt:
            for name in candidates:
                report.doubted[name] = teams_doubt
        elif not isinstance(sessions_dir, Path):
            # No transcripts to read: nothing can be shown never to have been
            # chatted with, so everyone is kept. The marker still records it.
            for name in candidates:
                report.doubted[name] = "no conversation log; removal needs chat history"
        else:
            _judge_each(cfg, candidates, sessions_dir, report, abandoned=abandoned)
    if report.doubted:
        logger.warning(
            "crewmate prune: %d crewmate(s) kept because their history could not be read: %s",
            len(report.doubted),
            "; ".join(f"{name}: {why}" for name, why in report.doubted.items()),
        )
    if report.refused:
        # A refused delete is not a commit: the row on disk was not the row
        # judged. Nothing is recorded as done; the next boot re-judges it.
        logger.warning(
            "crewmate prune: %d row(s) changed while being judged, pass not recorded: %s",
            len(report.refused),
            ", ".join(report.refused),
        )
        return report
    try:
        _write_marker(report)
    except FileExistsError:
        # Cannot happen while this process holds the lock; if it does, the
        # other marker is the record and this pass is already accounted for.
        report.skipped_marker = True
    return report
