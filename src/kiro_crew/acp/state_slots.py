"""Exclusive state directories for hosts whose on-disk state must not be shared.

Some hosts keep SQLite databases that tolerate only one live process at a time.
``codex app-server`` is one: every app-server on a host opens the same
``$CODEX_HOME/*.sqlite`` files, so a Codex Desktop daemon plus two Crew runtimes
fail new sessions with ``database is locked``.

A slot is a numbered directory under a root the harness names, held by an
exclusive advisory lock on a file inside it for as long as one runtime owns it.
The lowest free slot wins, so a restarted runtime reuses a directory whose
databases are already built instead of paying a fresh backfill each spawn. The
lock is released by closing its file, so a crashed gateway frees its slots with
no cleanup step.
"""

from __future__ import annotations

import contextlib
import logging
import os
import stat
from pathlib import Path

from kiro_crew import platform_compat

logger = logging.getLogger(__name__)

__all__ = ["MAX_STATE_SLOTS", "StateSlot", "acquire_state_slot"]

#: More live runtimes than this on one root means something leaks runtimes; the
#: caller falls back to the host's shared default rather than grow without bound.
MAX_STATE_SLOTS = 64

_LOCK_NAME = ".kirocrew-slot.lock"


class StateSlot:
    """One held slot: its directory, and the lock that keeps it exclusive."""

    def __init__(self, root: Path, path: Path, stack: contextlib.ExitStack) -> None:
        self.root = root
        self.path = path
        self._stack = stack

    def release(self) -> None:
        """Drop the lock. Safe to call twice."""
        self._stack.close()


def acquire_state_slot(root: Path) -> StateSlot:
    """Take the lowest free slot under *root*.

    A mkdir, an open and one non-blocking lock per slot tried, up to
    ``MAX_STATE_SLOTS`` of them: filesystem work, so callers on an event loop run
    it in a thread. Raises ``OSError`` when the root cannot be created, is a link,
    or no slot can be taken: every slot is held, or every one is unusable.

    A slot that cannot be used is skipped, never repaired: ``slot-N`` being a file,
    or its lock being a link or a hard-linked file, is the shape a same-UID
    process leaves behind on purpose, and a lock taken on a file that aliases
    another path is no lock at all. The lock is opened ``O_NOFOLLOW`` and checked
    for a regular, single-link inode before it is taken, as
    ``agent_state._locked`` does for the model-state sidecar.
    """
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if platform_compat.is_link_or_junction(root):
        raise OSError(f"state slot root {root} is a link")
    unusable: OSError | None = None
    for index in range(MAX_STATE_SLOTS):
        path = root / f"slot-{index}"
        stack = contextlib.ExitStack()
        try:
            path.mkdir(exist_ok=True, mode=0o700)
            if platform_compat.is_link_or_junction(path):
                raise OSError(f"{path} is a link")
            fd = os.open(
                path / _LOCK_NAME,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError(f"{path / _LOCK_NAME} is not a plain file")
            stack.enter_context(platform_compat.file_lock(fd, exclusive=True, wait=False))
        except BlockingIOError:
            # Held by a live runtime, here or in another gateway process.
            stack.close()
            continue
        except OSError as exc:
            stack.close()
            logger.warning("state slot %s skipped: %s", path, exc)
            unusable = exc
            continue
        except BaseException:
            stack.close()
            raise
        return StateSlot(root, path, stack)
    if unusable is not None:
        raise OSError(f"no usable state slot under {root} (last: {unusable})")
    raise OSError(f"all {MAX_STATE_SLOTS} state slots under {root} are held")
