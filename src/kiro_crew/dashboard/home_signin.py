"""The home's own Kiro sign-in, one click away (RFC one-chat first run §6.8 rule 5).

A home in the cloud signs in to Kiro with its OWN device-code sign-in; nothing is
copied from this machine, so each machine keeps its own session and refresh token.
The build then waits at "Sign in to Kiro" until the owner approves the code, and a
link on a card nobody is looking at is where a home stalls. So, while the home's
build shows a sign-in (``signin: {url, code}`` on the home card):

* When the owner pressed "Build my home" from a browser on this machine (the card
  records ``setup_aws_signin.browser_is_here`` at that click), the gateway opens
  the sign-in page once in that browser. kiro-cli prints the page with the user
  code already in it (``verification_uri_complete``, which
  ``cloud.login.parse_login_output`` prefers), so a browser already signed in to
  Kiro needs one confirmation. A page opens once per code, only from the watcher
  that click started (a watcher resumed after a restart opens nothing: the owner
  may have left this machine), and only on a known sign-in host.
* The chat that owns the card gets ONE ``home_signin`` system notice: the home
  waits for one click, and whether its page opened. It is not a model turn, so it
  costs no quota and raises no card (SC8), and it waits while that chat is
  mid-turn so it never lands between the rows of a reply.

The card's ``private`` flags only stop a repeat; they cannot start anything. The
decision to open is the in-process ``may_open`` of the click's own watcher, and
the page opened is the one this process's launch worker received from the home
(``launch_job.issued_signin``), never the job file's copy: that file is under
``run/``, which a sandboxed shell can write, so a prompt-injected agent could
otherwise have the gateway open a device code of its own for the owner to
approve. The host check below is a second fence, fed the same in-memory start URL.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from kiro_crew import setup_cards as sc
from kiro_crew.dashboard.system_notices import HOME_SIGNIN_KIND

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState

logger = logging.getLogger(__name__)

#: ``private`` keys: a digest of the last sign-in code whose page the gateway
#: tried to open, and whether the browser took it.
OPENED_KEY = "kiro_signin_opened"
OPEN_OK_KEY = "kiro_signin_open_ok"
#: ``private`` key: the notice has been posted for this card.
NOTICED_KEY = "kiro_signin_noticed"
#: ``private`` key: whether the owner's latest click on the card came from a
#: browser on this machine (``setup_aws_signin.browser_is_here``).
BROWSER_HERE_KEY = "browser_is_here"
#: The English text of the notice, the fallback for readers without the catalog.
_TEXT = {
    True: "Your home is waiting for one click to sign in to Kiro. The sign-in page "
    "opened in your browser.",
    False: "Your home is waiting for one click to sign in to Kiro. Open the sign-in "
    "link on its card.",
}
#: Where Kiro's own sign-in pages live: AWS Builder ID and Identity Center portals
#: (``*.awsapps.com``) and Kiro's portal (``*.kiro.dev``). An Identity Center portal
#: on a custom domain is admitted through the login target's own start URL.
_SIGNIN_HOST_SUFFIXES = (".awsapps.com", ".kiro.dev")
_SSO_DEVICE_HOST_RE = re.compile(r"^device\.sso\.[a-z0-9-]+\.amazonaws\.com\Z")


def openable(url: str, start_url: str = "") -> bool:
    """Whether *url* may be opened in the owner's browser without their click.

    HTTPS on the default port, no userinfo, on a Kiro sign-in host or the login
    target's own start-URL host. Anything else stays a link on the card, which
    the owner opens by choice.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or parts.username is not None or parts.password is not None:
        return False
    if port not in (None, 443):
        return False
    host = (parts.hostname or "").lower()
    if not host:
        return False
    if host == "kiro.dev" or host.endswith(_SIGNIN_HOST_SUFFIXES):
        return True
    if _SSO_DEVICE_HOST_RE.match(host):
        return True
    trusted = (urlsplit(start_url).hostname or "").lower() if start_url else ""
    return bool(trusted) and host == trusted


def _issued_here(job_id: str, url: str, code: str) -> bool:
    """Whether *url* and *code* are what this process's launch worker issued.

    The comparison and the host check both use the worker's in-memory copy, so
    nothing an agent writes to the job file can choose the page that opens.
    """
    from kiro_crew.cloud.launch_job import issued_signin

    issued = issued_signin(job_id) if job_id else None
    if issued is None:
        return False
    prompt, start_url = issued
    return prompt.url == url and prompt.code == code and openable(prompt.url, start_url)


def _digest(code: str, url: str) -> str:
    return hashlib.sha256(f"{code}\n{url}".encode("utf-8")).hexdigest()[:16]


def _claim(card_id: str, key: str, value: Any) -> bool:
    """Set ``private[key] = value`` unless it already holds *value*; whether it was set."""
    claimed = False

    def _mutate(c: sc.SetupCard) -> None:
        nonlocal claimed
        if c.private.get(key) == value:
            return
        c.private[key] = value
        claimed = True

    try:
        sc.update_card(card_id, _mutate)
    except sc.CardRejected:
        return False
    return claimed


async def record_browser_here(card: sc.SetupCard, same_machine: bool) -> sc.SetupCard:
    """Record on *card*, at the owner's click, whether their browser is on this machine."""
    from kiro_crew.dashboard.setup_aws_signin import browser_is_here

    here = await asyncio.to_thread(browser_is_here, same_machine)

    def _mutate(c: sc.SetupCard) -> None:
        c.private[BROWSER_HERE_KEY] = here

    return await asyncio.to_thread(sc.update_card, card.id, _mutate)


def _busy(slot: Any) -> bool:
    return bool(getattr(slot, "running", False) or getattr(slot, "_in_stage_execution", False))


async def prompt_signin(
    state: "DashboardState",
    card: sc.SetupCard,
    signin: dict[str, Any],
    *,
    job_id: str,
    may_open: bool,
) -> None:
    """Open the home's sign-in page (at most once per code) and post the one notice.

    Called on every poll of a home build that shows a sign-in; returns at once
    when both are already done. Never raises: the build watcher runs on after it.
    """
    try:
        url = str(signin.get("url") or "")
        if not url:
            return
        opened = False
        code = str(signin.get("code") or "")
        digest = _digest(code, url)
        if (
            may_open
            and not card.payload.get("simulated")
            and card.private.get(OPENED_KEY) != digest
            and _issued_here(job_id, url, code)
            and await asyncio.to_thread(_claim, card.id, OPENED_KEY, digest)
        ):
            from kiro_crew.cloud.login import _open_browser

            opened = bool(await asyncio.to_thread(_open_browser, url))
            await asyncio.to_thread(_claim, card.id, OPEN_OK_KEY, opened)
            logger.info(
                "home card %s: %s the home's Kiro sign-in page in this machine's browser",
                card.id,
                "opened" if opened else "could not open",
            )
        elif card.private.get(OPENED_KEY) == digest:
            opened = card.private.get(OPEN_OK_KEY) is True
        if card.private.get(NOTICED_KEY):
            return
        slot = state.get_slot(card.slot)
        if slot is None or _busy(slot):
            return
        if not await asyncio.to_thread(_claim, card.id, NOTICED_KEY, True):
            return
        slot.append(
            "assistant",
            _TEXT[opened],
            "msg msg-system",
            meta={"kind": HOME_SIGNIN_KIND, "opened": opened, "card": card.id},
        )
        push = getattr(state, "push_slots_update", None)
        if callable(push):
            push()
    except Exception:
        logger.warning("home card %s: Kiro sign-in prompt failed", card.id, exc_info=True)
