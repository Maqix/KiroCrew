"""The home card's "Sign in to AWS": the AWS CLI's own browser sign-in, from the card.

A home in the cloud is built in the owner's own AWS account, so the machine
running Kiro Crew needs an AWS CLI sign-in before the build. The card's
``aws_signin`` decision runs the CLI's own browser sign-in, ``aws login`` (AWS CLI
2.32+, OAuth 2.0 with PKCE, short-lived credentials), as a child of the gateway,
and watches for it to land:

* ``aws login`` opens the browser itself and takes the OAuth redirect on a
  loopback port of THIS machine, so it only works when the owner's browser is
  here too (:func:`browser_is_here`). Anywhere else the card is refused with the
  terminal command that works on that host, ``aws login --remote``.
* Kiro Crew never reads or stores the credentials (SC4): only the AWS CLI writes
  its own cache. The child's stdin, stdout and stderr are all closed, so nothing
  it prints can reach the card, the store or a log; the log gets its exit code.
  The outcome carries a state word and, once signed in, the account's last four
  digits.
* A watcher asks AWS who the profile signs in as (one read-only
  ``sts get-caller-identity``) every :data:`_POLL_SECS`. On success the card is
  ``pending`` again with ``aws_signed_in: true`` in its OUTCOME (the payload the
  owner saw is immutable) and the owner presses Build. After
  :data:`SIGNIN_WAIT_SECS` it gives up. The child is stopped on timeout, on
  cancel, and whenever the card leaves this sign-in.

The live child is held in process memory, keyed by card id: the card store is
writable from the agent's sandbox, so nothing on disk names a process to signal.
A gateway restart loses the watcher; the card's ``expires_ts`` then lets the owner
start again (:func:`_stale`).
"""

from __future__ import annotations

import asyncio
import atexit
import hmac
import logging
import re
import secrets
import subprocess
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kiro_crew import setup_cards as sc
from kiro_crew.dashboard import setup_flow as sf

if TYPE_CHECKING:  # pragma: no cover
    from kiro_crew.dashboard.state import DashboardState

logger = logging.getLogger(__name__)

#: How long the card waits for the owner to finish the browser sign-in: the AWS
#: CLI's own wait, which also leaves time to create an account first.
SIGNIN_WAIT_SECS = 600
#: How often the watcher checks the child and asks AWS who the profile is.
_POLL_SECS = 4.0
#: A waiting card this long past its ``expires_ts`` has no watcher left (the
#: gateway restarted), so the owner may start the sign-in again.
_STALE_GRACE_SECS = 30
#: AWS already answers as signed in while the child is still running: how long
#: it may take to finish writing its own cache and config before it is stopped.
_EXIT_GRACE_SECS = 15.0
#: How long a stopped child gets to exit before its whole process group is.
_STOP_GRACE_SECS = 5.0
_STS_TIMEOUT_SECS = 15
_VERSION_TIMEOUT_SECS = 15
#: The first AWS CLI release with ``aws login``.
MIN_CLI_VERSION = (2, 32)
#: The AWS CLI's exit status for a usage error, which is what a CLI without the
#: ``login`` command answers.
_CLI_USAGE_ERROR = 252
#: ``aws login``'s exit status for a profile that already holds access keys: it
#: refuses at once and names a new profile to sign in under instead.
_CLI_PROFILE_HAS_KEYS = 253
_CLI_VERSION_RE = re.compile(r"aws-cli/(\d+)\.(\d+)")

CODE_REMOTE = "aws_signin_remote"
CODE_TIMEOUT = "aws_signin_timeout"
CODE_FAILED = "aws_signin_failed"
CODE_BUSY = "aws_signin_busy"
CODE_NOT_NEEDED = "aws_signin_not_needed"
CODE_CLI_MISSING = "aws_cli_missing"
CODE_CLI_TOO_OLD = "aws_cli_too_old"
CODE_PROFILE_HAS_KEYS = "aws_signin_profile_has_keys"

_TOO_OLD_MESSAGE = (
    "signing in from the card needs AWS CLI 2.32 or newer (`aws login`); "
    "update the AWS CLI, then try again"
)

#: The profile the agent proposes a home under when the card's profile holds
#: access keys (``crew-setup`` SKILL.md names it too).
NEW_PROFILE = "kirocrew"

#: Outcome keys this module owns; everything else on the outcome is kept.
_OWN_KEYS = ("aws_signin", "aws_signed_in", "aws_account")


@dataclass
class _SignIn:
    card_id: str
    #: Matches ``card.private["aws_signin_run"]``: a watcher settles only its own run.
    run: str
    proc: "subprocess.Popen[bytes]"
    #: ``time.monotonic()`` past which the watcher gives up.
    deadline: float
    task: "asyncio.Task[None] | None" = field(default=None, repr=False)


#: The live sign-in per card. Process memory only (see the module doc).
_live: dict[str, _SignIn] = {}
#: The watchers, held so a running one is never garbage collected.
_tasks: set["asyncio.Task[None]"] = set()


# ── where the browser is ────────────────────────────────────────────────────


def browser_is_here(same_machine: bool) -> bool:
    """Whether ``aws login`` can open its sign-in page where the owner is.

    Three facts, all required. *same_machine*: the owner's request came straight
    from this machine's loopback with no forwarding header
    (``origin.is_direct_local_request``), so their browser is here and not behind
    a tunnel or proxy. The install shape (``auth.shape.detect_shape``, the same
    call that picks the Kiro sign-in's transport) is a desktop, not an SSH
    session or a container, whose loopback is not the owner's. And this process
    can open a browser at all (no display on a Linux host means it cannot).
    """
    from kiro_crew.auth.shape import InstallShape, detect_shape
    from kiro_crew.cloud.login import _browser_open_supported

    return same_machine and detect_shape() is InstallShape.DESKTOP and _browser_open_supported()


def terminal_command(profile: str) -> str:
    """The sign-in that works on a host with no browser: a URL out, a code back."""
    return "aws login --remote" + (f" --profile {profile}" if profile else "")


# ── the AWS CLI ─────────────────────────────────────────────────────────────


def _signed_in_account(profile: str) -> str | None:
    """The account's last four digits when AWS answers for *profile*, else ``None``.

    The same read-only check the first run makes (``cloud.local_signin.detect``).
    """
    from kiro_crew.cloud.local_signin import detect

    found = detect(profile, timeout=_STS_TIMEOUT_SECS)
    return found.account_hint if found is not None else None


def _cli_problem() -> tuple[str, str] | None:
    """``(code, message)`` when this machine's AWS CLI cannot run ``aws login``."""
    from kiro_crew.cloud import aws

    try:
        rc, out, err = aws.run_aws(["--version"], timeout=_VERSION_TIMEOUT_SECS)
    except Exception:
        logger.debug("AWS CLI version check failed", exc_info=True)
        return None
    if rc == 127:
        return (
            CODE_CLI_MISSING,
            "the AWS CLI is not installed on this computer; install AWS CLI 2.32 or newer, "
            "then try again",
        )
    match = _CLI_VERSION_RE.search(f"{out} {err}")
    if match and (int(match.group(1)), int(match.group(2))) < MIN_CLI_VERSION:
        return (CODE_CLI_TOO_OLD, _TOO_OLD_MESSAGE)
    return None


def _spawn_login(profile: str, region: str) -> "subprocess.Popen[bytes]":
    """Start the AWS CLI's own browser sign-in for *profile*.

    The argv is the resolved ``aws`` binary, the literal ``login``, and the
    profile and region the owner saw on the card, both re-validated by
    ``setup_cards.build_home``. ``--region`` answers the one question ``aws login``
    asks a profile that has no region, which a child with no stdin cannot. All
    three standard streams are closed: the child talks to the browser, never to
    this process. Its own session, so a stop can reach anything it started.
    """
    from kiro_crew.cloud import aws
    from kiro_crew.deploy.engine import aws_spawn_env, resolve_aws_bin
    from kiro_crew.sandbox import scrub_env

    aws.assert_human_action("login")
    argv = [resolve_aws_bin(), "login"]
    if profile:
        argv += ["--profile", profile]
    if region:
        argv += ["--region", region]
    return subprocess.Popen(  # noqa: S603 — fixed argv, no shell
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=scrub_env(aws_spawn_env(argv[0])),
    )


def _stop(proc: "subprocess.Popen[bytes]") -> None:
    """End the child, then its process group if it does not exit in time.

    ``aws login`` opens the browser detached from its own group, so the group
    stop reaches the CLI and nothing the owner is looking at.
    """
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=_STOP_GRACE_SECS)
    except subprocess.TimeoutExpired:
        from kiro_crew.cloud.ssm import kill_port_forward

        kill_port_forward(proc)
    except OSError:
        logger.debug("stopping aws login failed", exc_info=True)


# ── card state ──────────────────────────────────────────────────────────────


def _target(card: sc.SetupCard) -> tuple[str, str]:
    """The profile and region to sign in, from the payload the owner saw."""
    p = card.payload
    profile = str(p.get("profile") or "")
    raw_size = p.get("size")
    size: dict[str, Any] = raw_size if isinstance(raw_size, dict) else {}
    settings = sc.build_home(
        {
            "region": p.get("region"),
            "profile": "" if profile == "default" else profile,
            "size": size.get("key"),
        }
    )
    return settings["profile"], settings["region"]


def _base(card: sc.SetupCard) -> dict[str, Any]:
    return {k: v for k, v in (card.outcome or {}).items() if k not in _OWN_KEYS}


def _signing_in(card: sc.SetupCard) -> bool:
    signin = (card.outcome or {}).get("aws_signin")
    return (
        card.status == sc.STATUS_WAITING
        and isinstance(signin, dict)
        and signin.get("state") == "waiting"
    )


def _stale(card: sc.SetupCard) -> bool:
    """A sign-in whose watcher is gone: past its time, with no child in this process."""
    if not _signing_in(card) or card.id in _live:
        return False
    expires = (card.outcome or {}).get("aws_signin", {}).get("expires_ts")
    return not isinstance(expires, (int, float)) or time.time() > expires + _STALE_GRACE_SECS


def _check_hash(card: sc.SetupCard, card_hash: str) -> None:
    if not hmac.compare_digest(str(card_hash), card.payload_hash):
        raise sc.CardRejected("this card changed since it was shown", "card_hash_mismatch")


def _claim(card_id: str, card_hash: str) -> sc.SetupCard:
    """``claim_pending``, which also admits a stale sign-in so it can start over."""

    def _mutate(c: sc.SetupCard) -> None:
        _check_hash(c, card_hash)
        if c.status != sc.STATUS_PENDING and not _stale(c):
            raise sc.CardRejected("this card is not waiting for a decision", "card_not_pending")
        c.status = sc.STATUS_WORKING
        c.error = None

    return sc.update_card(card_id, _mutate)


async def _settle(
    card_id: str, run: str, outcome: dict[str, Any], error: tuple[str, str] | None
) -> sc.SetupCard | None:
    """Return the card to ``pending``, only if it is still waiting on THIS run."""
    settled = False

    def _mutate(c: sc.SetupCard) -> None:
        nonlocal settled
        if not _signing_in(c) or c.private.get("aws_signin_run") != run:
            return
        c.status = sc.STATUS_PENDING
        c.outcome = outcome
        c.error = {"code": error[0], "message": error[1]} if error else None
        c.private.pop("aws_signin_run", None)
        settled = True

    try:
        card = await asyncio.to_thread(sc.update_card, card_id, _mutate)
    except sc.CardRejected:
        return None
    return card if settled else None


def _signed_in_outcome(base: dict[str, Any], account: str) -> dict[str, Any]:
    return {**base, "aws_signed_in": True, "aws_account": account, "aws_signin": {"state": "done"}}


# ── the decision ────────────────────────────────────────────────────────────


async def decide_signin(
    state: "DashboardState",
    card: sc.SetupCard,
    card_hash: str,
    input_: dict[str, Any],
    *,
    same_machine: bool,
) -> sc.SetupCard:
    """The home card's ``aws_signin`` decision: start the sign-in, or cancel it.

    ``input.cancel`` stops a sign-in in progress and returns the card to
    ``pending``. A failure to start leaves the card ``pending`` with the reason;
    the home card itself stays open, since the owner may still build it later.
    """
    if card.kind != sc.KIND_HOME:
        raise sc.CardRejected("only a home card signs in to AWS", "invalid_decision")
    denial = await asyncio.to_thread(sf._governance_denial, card.kind, card.session_key)
    if denial:
        raise sc.CardRejected(f"blocked by policy: {denial}", "governance_denied")
    if input_.get("cancel") is True:
        card = await _cancel(card, card_hash)
        outcome = "cancelled"
    else:
        card = await asyncio.to_thread(_claim, card.id, card_hash)
        sf.broadcast(state, card)
        try:
            card = await _start(state, card, same_machine=same_machine)
        except sc.CardRejected as exc:
            card = await sf._back_to_pending(card, exc.code, str(exc))
        except Exception:
            logger.exception("home card %s: the AWS sign-in could not start", card.id)
            card = await sf._back_to_pending(
                card, CODE_FAILED, "the AWS sign-in could not start; see the gateway log"
            )
        outcome = (card.error or {}).get("code") or ("waiting" if _signing_in(card) else "done")
    sf.broadcast(state, card)
    sf._audit("setup_card.aws_signin", outcome, card.session_key, f"kind:home card:{card.id}")
    return card


async def _start(
    state: "DashboardState", card: sc.SetupCard, *, same_machine: bool
) -> sc.SetupCard:
    if card.payload.get("simulated") or str(card.private.get("phase") or "build") != "build":
        raise sc.CardRejected("this home needs no AWS sign-in", CODE_NOT_NEEDED)
    profile, region = _target(card)
    base = _base(card)
    account = await asyncio.to_thread(_signed_in_account, profile)
    if account is not None:
        card = await sf._finish(card, sc.STATUS_PENDING, outcome=_signed_in_outcome(base, account))
        return await sf.refresh_home_payload(card)
    if not await asyncio.to_thread(browser_is_here, same_machine):
        return await sf._finish(
            card,
            sc.STATUS_PENDING,
            outcome={
                **base,
                "aws_signin": {"state": "remote", "command": terminal_command(profile)},
            },
            error=(
                CODE_REMOTE,
                "Kiro Crew cannot open the AWS sign-in page on the computer it runs on. In a "
                f"terminal on that computer, run `{terminal_command(profile)}`, then build "
                "your home",
            ),
        )
    if any(other != card.id for other in _live):
        raise sc.CardRejected(
            "an AWS sign-in is already open on another card; finish or cancel it first",
            CODE_BUSY,
        )
    problem = await asyncio.to_thread(_cli_problem)
    if problem is not None:
        raise sc.CardRejected(problem[1], problem[0])
    from kiro_crew.cloud.aws import CloudActionDenied

    try:
        proc = await asyncio.to_thread(_spawn_login, profile, region)
    except FileNotFoundError:
        raise sc.CardRejected(
            "the AWS CLI is not installed on this computer", CODE_CLI_MISSING
        ) from None
    except CloudActionDenied as exc:
        raise sc.CardRejected(str(exc), CODE_FAILED) from None
    _stop_at_exit()
    run = secrets.token_hex(8)
    signin = _SignIn(card.id, run, proc, time.monotonic() + SIGNIN_WAIT_SECS)
    _live[card.id] = signin

    def _remember(c: sc.SetupCard) -> None:
        c.private["aws_signin_run"] = run

    try:
        await asyncio.to_thread(sc.update_card, card.id, _remember)
        card = await sf._finish(
            card,
            sc.STATUS_WAITING,
            outcome={
                **base,
                "aws_signin": {"state": "waiting", "expires_ts": time.time() + SIGNIN_WAIT_SECS},
            },
        )
    except BaseException:
        _forget(signin)
        _terminate_now(proc)
        raise
    logger.info("home card %s: aws login started", card.id)
    signin.task = asyncio.create_task(_watch(state, signin, profile, base))
    _tasks.add(signin.task)
    signin.task.add_done_callback(_tasks.discard)
    return card


async def _cancel(card: sc.SetupCard, card_hash: str) -> sc.SetupCard:
    _check_hash(card, card_hash)
    if not _signing_in(card):
        raise sc.CardRejected("this card is not waiting for an AWS sign-in", "card_not_pending")
    signin = _live.pop(card.id, None)
    if signin is not None:
        await asyncio.to_thread(_stop, signin.proc)
    cancelled = False

    def _mutate(c: sc.SetupCard) -> None:
        nonlocal cancelled
        _check_hash(c, card_hash)
        if not _signing_in(c):
            return
        c.status = sc.STATUS_PENDING
        c.outcome = _base(c)
        c.error = None
        c.private.pop("aws_signin_run", None)
        cancelled = True

    card = await asyncio.to_thread(sc.update_card, card.id, _mutate)
    if not cancelled:
        raise sc.CardRejected("this card is not waiting for an AWS sign-in", "card_not_pending")
    return card


# ── the watcher ─────────────────────────────────────────────────────────────


async def _await_exit(proc: "subprocess.Popen[bytes]", secs: float) -> None:
    deadline = time.monotonic() + secs
    while proc.poll() is None and time.monotonic() < deadline:
        await asyncio.sleep(0.5)


_Verdict = tuple[dict[str, Any], "tuple[str, str] | None"]


async def _follow(signin: _SignIn, profile: str, base: dict[str, Any]) -> _Verdict | None:
    """Poll until AWS answers, the child ends or time runs out; ``None`` if the card moved."""
    card_id, proc = signin.card_id, signin.proc
    while True:
        await asyncio.sleep(_POLL_SECS)
        card = await asyncio.to_thread(sc.get_card, card_id)
        if card is None or not _signing_in(card) or _live.get(card_id) is not signin:
            return None
        exited = proc.poll() is not None
        account = await asyncio.to_thread(_signed_in_account, profile)
        if account is not None:
            await _await_exit(proc, _EXIT_GRACE_SECS)
            return _signed_in_outcome(base, account), None
        if exited:
            logger.info("home card %s: aws login exited with code %s", card_id, proc.returncode)
            if proc.returncode == _CLI_USAGE_ERROR:
                return base, (CODE_CLI_TOO_OLD, _TOO_OLD_MESSAGE)
            if proc.returncode == _CLI_PROFILE_HAS_KEYS:
                # Retrying cannot help: the card's profile is fixed by its payload,
                # so the agent proposes a new home card under another profile.
                return base, (
                    CODE_PROFILE_HAS_KEYS,
                    "this AWS profile holds access keys, so it cannot use a browser sign-in; "
                    f"ask in the chat for a home under a new profile name such as {NEW_PROFILE}",
                )
            return base, (
                CODE_FAILED,
                "the AWS sign-in did not finish; press Sign in to AWS to try again",
            )
        if time.monotonic() >= signin.deadline:
            logger.info("home card %s: aws login timed out", card_id)
            return base, (
                CODE_TIMEOUT,
                "the AWS sign-in was not finished in time; press Sign in to AWS to try again",
            )


async def _watch(
    state: "DashboardState", signin: _SignIn, profile: str, base: dict[str, Any]
) -> None:
    """Follow one sign-in to its end, stop its child, then return the card to the owner."""
    try:
        verdict = await _follow(signin, profile, base)
    except asyncio.CancelledError:
        _forget(signin)
        _terminate_now(signin.proc)
        raise
    except Exception:
        logger.exception("home card %s: AWS sign-in watcher failed", signin.card_id)
        verdict = base, (CODE_FAILED, "the AWS sign-in stopped; press Sign in to AWS to try again")
    _forget(signin)
    await asyncio.to_thread(_stop, signin.proc)
    if verdict is None:
        return
    card = await _settle(signin.card_id, signin.run, *verdict)
    if card is not None:
        if verdict[1] is None:
            # Signed in: the card now shows the account's region, plan and sizes.
            card = await sf.refresh_home_payload(card)
        sf.broadcast(state, card)
        outcome = verdict[1][0] if verdict[1] else "signed_in"
        sf._audit("setup_card.aws_signin", outcome, card.session_key, f"kind:home card:{card.id}")


def _forget(signin: _SignIn) -> None:
    if _live.get(signin.card_id) is signin:
        del _live[signin.card_id]


def _terminate_now(proc: "subprocess.Popen[bytes]") -> None:
    """A stop that cannot wait: the loop is going away, so no thread hop."""
    if proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass


def _stop_all_now() -> None:
    for signin in list(_live.values()):
        _terminate_now(signin.proc)


_exit_hook_registered = False


def _stop_at_exit() -> None:
    """End every live child when the gateway exits; its own session would outlive it."""
    global _exit_hook_registered
    if not _exit_hook_registered:
        atexit.register(_stop_all_now)
        _exit_hook_registered = True
