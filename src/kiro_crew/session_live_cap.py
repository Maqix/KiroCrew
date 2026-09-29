"""How many chat sessions may keep a live process at once (``session.max_live_sessions``).

Each live chat is a kiro-cli child plus the MCP servers its agent spec starts,
and before this cap nothing bounded how many stayed up: the idle sweep retires
one only after ``session.timeout_secs``. On a 2 GB cloud home three or four open
chats run the host out of memory.

This module is the arithmetic. The release it sizes is
``session_cleanup.SessionCleanup.make_room``, called from
``SessionManager.get_or_create`` before a chat cold-starts; see session.md
"Live chat cap".

The auto cap is sized from TOTAL memory, not from the available-memory reading
``subagent.compute_max_subagents`` uses. That cap sizes a burst of workers
against what is free at the moment it is resolved; this one is a count held for
the gateway's life, and a snapshot of available memory is the wrong input for
it twice over: the chats already live have been subtracted from it, and on a
laptop it swings with whatever else is open, so a busy moment at boot would pin
a 16 GB machine to a small-home cap. Total memory is what tells a 2 GB home from
a 16 GB laptop, which is the one distinction the cap has to make.
"""

from __future__ import annotations

from kiro_crew import platform_compat

#: One live chat, measured on a 2 GB home (2026-09-28): kiro-cli ~230 MB plus
#: its core and cron MCP servers at ~80 MB each.
LIVE_SESSION_COST_MIB = 400
#: What the host holds before any chat: the OS, the gateway with its embedding
#: runtime (~940 MB), and the shared background runtime (~270 MB).
LIVE_SESSION_RESERVE_MIB = 1536
#: The auto cap never goes below the main chat plus one other.
LIVE_SESSIONS_AUTO_FLOOR = 2
#: Nor above this, which a 32 GB host reaches. Past it an idle chat is released
#: rather than kept, which costs its next reply a resume and nothing else.
LIVE_SESSIONS_AUTO_CEILING = 64


def auto_max_live_sessions(total_mib: int) -> int:
    """The auto cap for a host with *total_mib* of RAM.

    ``(total - reserve) // cost``, clamped to ``[floor, ceiling]``. An unreadable
    total (``<= 0``) answers the ceiling: a host whose size is unknown keeps
    today's uncapped behaviour rather than a small-home cap it may not need.
    """
    if total_mib <= 0:
        return LIVE_SESSIONS_AUTO_CEILING
    fit = (total_mib - LIVE_SESSION_RESERVE_MIB) // LIVE_SESSION_COST_MIB
    return max(LIVE_SESSIONS_AUTO_FLOOR, min(LIVE_SESSIONS_AUTO_CEILING, fit))


def resolve_max_live_sessions(configured: object) -> int:
    """The cap in force for a configured ``session.max_live_sessions``.

    A positive int is the cap as written; ``0``, and anything that is not a real
    int (a ``MagicMock`` config converts to 1 through ``int()``), is auto.
    """
    if isinstance(configured, int) and not isinstance(configured, bool) and configured > 0:
        return configured
    return auto_max_live_sessions(platform_compat.host_total_mib())
