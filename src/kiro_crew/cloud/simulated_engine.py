"""A launch engine that walks a real launch's steps without touching AWS.

For reviewing and testing the first-run home flow before anyone spends money:
it pauses for plausible durations at each step, returns a fake instance id, and
registers nothing. It is selected only when ``KIROCREW_CLOUD_SIMULATE=1`` is set
on the gateway's own environment (see ``dashboard/server.py``), and every
surface that shows a launch from it labels it as simulated.
"""

from __future__ import annotations

import logging
import os
import threading

from kiro_crew.constants import ENV_TRUTHY

logger = logging.getLogger(__name__)

#: Gateway env var that swaps the launch engine for this simulation.
SIMULATE_ENV = "KIROCREW_CLOUD_SIMULATE"
#: Seconds each simulated step takes; small enough for a demo, long enough to
#: show that the chat carries on while the home builds.
_STEP_SECS = float(os.environ.get("KIROCREW_CLOUD_SIMULATE_STEP_SECS", "6"))
SIMULATED_INSTANCE_ID = "i-0simulated0000000"


def simulation_enabled() -> bool:
    return os.environ.get(SIMULATE_ENV, "").strip().lower() in ENV_TRUTHY


class _SignedIn:
    """A sign-in that is already complete: the simulation has no remote login."""

    already_logged_in = True
    url = ""
    code = ""
    ports: list = []
    error = ""

    def wait(self, cancel: threading.Event) -> bool:
        return True

    def close(self) -> None:
        pass

    def abort(self) -> bool:
        """Nothing to stop: no remote login was started."""
        return True


class SimulatedLaunchEngine:
    """``LaunchEngine`` for demos and tests; see the module docstring."""

    simulated = True

    def __init__(self, step_secs: float | None = None) -> None:
        self._step = _STEP_SECS if step_secs is None else step_secs

    def _pause(self) -> None:
        threading.Event().wait(self._step)

    def preflight(self, profile: str, region: str) -> None:
        logger.warning("SIMULATED cloud launch: preflight (no AWS call is made)")
        self._pause()

    def provision(self, *, tag: str, size_key: str, profile: str, region: str, **_: object) -> str:
        logger.warning("SIMULATED cloud launch: provision %s (%s)", tag, size_key)
        self._pause()
        self._pause()
        return SIMULATED_INSTANCE_ID

    def begin_signin(self, *, instance_id: str, profile: str, region: str, login_target=None) -> _SignedIn:  # type: ignore[no-untyped-def]
        self._pause()
        return _SignedIn()

    def register(self, *, instance_id: str, tag: str, profile: str, region: str) -> None:
        logger.warning("SIMULATED cloud launch: register skipped for %s", instance_id)
        self._pause()

    def teardown(self, *, tag: str, profile: str, region: str) -> bool:
        return True
