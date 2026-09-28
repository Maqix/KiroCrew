"""The one-chat first run: its session marker and the state that records progress.

A fresh install gets ONE pinned chat session, created by the gateway on its first
start, in which a deterministic privacy card is shown before the first model turn
and the agent then walks the user through setup with setup cards
(:mod:`kiro_crew.setup_cards`). This module owns the small state file that says
which session that is and which stages are done.

The state file GATES NOTHING. It steers what the agent suggests next and which
slot ``kirocrew start`` opens; every permission-bearing fact (privacy
acknowledgement, onboarding flags, connections, jobs) lives in its own owner and
is read from there. A forged or deleted state file therefore changes
suggestions, never what the agent may do.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kiro_crew.atomic_write import atomic_write
from kiro_crew.config.paths import data_home

logger = logging.getLogger(__name__)

#: Directory under the data home holding first-run and setup-card state.
SETUP_DIR_NAME = "setup"
#: The first-run state file, inside :data:`SETUP_DIR_NAME`.
FIRST_RUN_FILE = "first-run.json"
#: Largest state file read back; anything bigger is treated as absent.
_MAX_STATE_BYTES = 64 * 1024
#: Stage names the agent may record. A closed set so a hand-edited file cannot
#: grow unbounded keys the kickoff prompt would echo.
STAGES: tuple[str, ...] = (
    "privacy",
    "hello",
    "import",
    "connect",
    "channel",
    "preview",
    "job_kept",
    "stay_on",
)


def setup_dir() -> Path:
    """The data-home directory holding setup state (created on demand)."""
    path = data_home() / SETUP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def first_run_state_path() -> Path:
    return setup_dir() / FIRST_RUN_FILE


def read_state() -> dict[str, Any]:
    """Return the first-run state, or ``{}`` when absent or unreadable."""
    path = data_home() / SETUP_DIR_NAME / FIRST_RUN_FILE
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError:
        logger.warning("first-run state unreadable at %s", path, exc_info=True)
        return {}
    if len(raw) > _MAX_STATE_BYTES:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_first_run_slot() -> str | None:
    """The first-run session's slot key, or ``None`` when there is none."""
    slot = read_state().get("slot")
    return slot if isinstance(slot, str) and slot else None


def write_state(state: dict[str, Any]) -> None:
    """Persist *state* atomically. Blocking: call off the event loop.

    Replaces the whole file; a change to one key goes through
    :func:`update_state`, so two writers cannot drop each other's keys.
    """
    payload = json.dumps(state, sort_keys=True, indent=2) + "\n"
    atomic_write(first_run_state_path(), payload, mode=0o600)


#: Serializes read-change-write in this process. Several writers share the
#: file (stages, the main chat, first-week tips, held hand-off notices) from
#: the loop and from worker threads; without it, two at once lose one change.
#: The section is one small read and one atomic rename. ``kirocrew start``
#: writes only ``home``, before the gateway creates the session.
_STATE_LOCK = threading.Lock()


def update_state(change: Callable[[dict[str, Any]], bool | None]) -> dict[str, Any]:
    """Read the state, let *change* edit it in place, and write it back.

    *change* returns ``False`` to skip the write (nothing changed). Returns the
    state as written, or as read when skipped. Blocking: call off the loop.
    """
    with _STATE_LOCK:
        state = read_state()
        if change(state) is not False:
            write_state(state)
        return state


def record_slot(slot_key: str) -> dict[str, Any]:
    """Record *slot_key* as the first-run session and return the new state.

    Keeps a ``home`` choice ``kirocrew start`` recorded before the gateway
    created the session.
    """

    def _fresh(state: dict[str, Any]) -> None:
        home = state.get("home")
        state.clear()
        state.update({"slot": slot_key, "created_ts": time.time(), "stages": {}})
        if isinstance(home, dict):
            state["home"] = home

    return update_state(_fresh)


#: Where the owner said the crew should live, as ``kirocrew start`` records it.
HOME_CHOICES: tuple[str, ...] = ("here", "cloud", "later")


def record_home_choice(choice: str, *, region: str = "", profile: str = "", size: str = "") -> None:
    """Record the Egg's home answer. Presentation only: it decides whether the
    first-run chat SHOWS a home card, and the card's own click decides the rest."""
    if choice not in HOME_CHOICES:
        raise ValueError(f"home choice must be one of {HOME_CHOICES}")
    home: dict[str, Any] = {"choice": choice}
    for key, value in (("region", region), ("profile", profile), ("size", size)):
        if value:
            home[key] = value
    update_state(lambda state: state.__setitem__("home", home))


def mark_stage(stage: str) -> dict[str, Any]:
    """Mark *stage* done (idempotent) and return the new state.

    Unknown stage names are ignored rather than stored, so the file keeps the
    closed :data:`STAGES` vocabulary.
    """

    def _mark(state: dict[str, Any]) -> bool:
        if stage not in STAGES or not state.get("slot"):
            return False
        stages = state.get("stages")
        if not isinstance(stages, dict):
            stages = {}
        if stage in stages:
            return False
        stages[stage] = time.time()
        state["stages"] = stages
        return True

    return update_state(_mark)


def done_stages() -> list[str]:
    stages = read_state().get("stages")
    if not isinstance(stages, dict):
        return []
    return [name for name in STAGES if name in stages]


def is_first_run_slot(slot_key: str) -> bool:
    """Whether *slot_key* is the recorded first-run session.

    For presentation only (which tab to open, which slot to pin). The state file
    is agent-writable, so this must never admit or refuse an action.
    """
    return bool(slot_key) and read_first_run_slot() == slot_key


def record_main(slot_key: str) -> None:
    """Record *slot_key* as the main chat: where the product opens by default.

    Presentation only, like everything in this file: it decides which chat is
    opened and which one carries the crew overview, never what a turn may do.
    """
    update_state(lambda state: state.__setitem__("main", slot_key))


def read_main_slot() -> str | None:
    """The main chat's slot key, or ``None`` when no chat is the main one."""
    slot = read_state().get("main")
    return slot if isinstance(slot, str) and slot else None


#: Key of the handoff notices the main chat is still owed (``dashboard/handoff_notice.py``).
_HANDOFF_OWED_KEY = "handoff_owed"


def read_handoff_owed() -> dict[str, dict[str, dict[str, str]]]:
    """Handoff notices held back while the main chat ran, by main chat then chat.

    Kept here so a restart during the main chat's turn does not lose them. Only
    string fields survive the read; the notice module checks the rest.
    """
    raw = read_state().get(_HANDOFF_OWED_KEY)
    if not isinstance(raw, dict):
        return {}
    owed: dict[str, dict[str, dict[str, str]]] = {}
    for main, notices in raw.items():
        if not isinstance(main, str) or not isinstance(notices, dict):
            continue
        kept = {
            child: {k: v for k, v in meta.items() if isinstance(k, str) and isinstance(v, str)}
            for child, meta in notices.items()
            if isinstance(child, str) and isinstance(meta, dict)
        }
        if kept:
            owed[main] = kept
    return owed


def write_handoff_owed(owed: dict[str, dict[str, dict[str, str]]]) -> None:
    """Replace the held handoff notices with *owed*; empty drops the key."""

    def _replace(state: dict[str, Any]) -> bool:
        if owed:
            state[_HANDOFF_OWED_KEY] = owed
            return True
        return state.pop(_HANDOFF_OWED_KEY, None) is not None

    update_state(_replace)
