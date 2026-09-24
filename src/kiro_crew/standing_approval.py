"""The operator's STANDING declaration that every tool approval is skipped.

``SafetyOverride`` has two kinds of grant. An **ad-hoc** one is toggled mid-session
and expires on ``agent.yolo_duration``. A **declared** one is the operator's standing
instruction: it never expires, and it is re-established on every startup. This module
owns where that declaration is read from, and nothing else -- establishing the grant
stays with ``safety_override``.

Where the declaration lives, and why not ``config.json``
-------------------------------------------------------
``standing-approval/grant.json`` sits on the KEYSTONE floor
(``security._CREW_SECRET_LEAVES``) and is bind-masked out of every agent sandbox
(``sandbox._CREW_HIDDEN_LEAVES``). That is the same placement as
``computer_use.json``, ``aws_service_consent.json``, ``oauth_endpoints.json``,
``file_delivery_consent.json`` and ``ssh_auth_sock_consent.json``, and for the same
reason: this is an authorization, not a preference. It is the widest one the product
has -- every tool call in every future session, with no prompt and no expiry.

The declaration is deliberately NOT ``agent.dangerously_skip_permissions`` in
``config.json``. A read-only seal on that document closes a write to the sealed NAME,
and it is kept as defence in depth, but a seal cannot reach the inode behind the name:
the crew data-home root is writable in every sandbox, ``link(2)`` needs no write
permission on the file it copies a name for, and a bind mount seals a MOUNT rather
than an inode. So an in-sandbox process that owns that document can add a second name
for it in the writable root, write the standing posture through that name, and unlink
it -- and the next startup reads a poisoned document under a single link.

Two properties of this keystone answer that, and only the pair does:

* **Masked, not sealed.** A masked leaf cannot be opened in-sandbox at all, so it is
  not a ``link(2)`` source. A sealed-but-readable one still is.
* **A directory, not a file.** Linux refuses ``link(2)`` on a directory outright, so
  the alias shape has no source here even in principle, and a directory bind covers
  every child name rather than one pinned inode.

How the operator grants it
--------------------------
Like ``oauth_endpoints.json``, the operator writes the leaf out-of-band, from outside
the agent sandbox::

    mkdir -p "$KIROCREW_HOME/standing-approval"
    printf '{"dangerously_skip_permissions": true}\\n' \\
        > "$KIROCREW_HOME/standing-approval/grant.json"

There is deliberately no dashboard toggle (there never was one for this switch) and
no CLI verb: a surface that records this grant on request is a grant an automated
caller can take. This module is READ-ONLY on purpose.

Migrating an existing declaration
---------------------------------
An operator who set ``agent.dangerously_skip_permissions: true`` in ``config.json``
loses the standing grant until they write the keystone. That break is deliberate and
it is announced rather than silent: :func:`migration_notice` returns the words a
startup logs when the retired key is still set and the keystone is absent, and the
startup grants NOTHING in that state. Silently honouring the old location would keep
exactly the writable declaration this move exists to retire.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kiro_crew import sandbox
from kiro_crew.atomic_write import atomic_write
from kiro_crew.config.loader import standing_approval_path

logger = logging.getLogger(__name__)

#: The field inside the keystone document. Spelled the same as the retired
#: ``config.json`` key on purpose: an operator moving the declaration copies one line
#: rather than learning a second name for the same switch.
GRANT_FIELD: str = "dangerously_skip_permissions"

#: Size bound for the grant document. It holds one boolean and an optional timestamp,
#: so a few hundred bytes is generous; anything larger is not a legitimate grant. The
#: bound is what stops a huge file at this name from being buffered into the gateway's
#: memory on a synchronous boot read.
_MAX_GRANT_BYTES: int = 4096

#: The self-declared timestamp field inside the grant document. It is NOT trusted on its
#: own -- a pre-upgrade agent writes any value -- but it is folded into the provenance
#: binding so that re-declaring the grant with a fresh timestamp is a distinct
#: establishment the gateway records anew.
_GRANTED_AT_FIELD: str = "granted_at"

#: The gateway-held provenance record, a SIBLING of ``grant.json`` inside the same
#: ``standing-approval/`` keystone directory. That placement is the whole trust anchor:
#: the directory is bind-masked out of every agent sandbox (``sandbox._CREW_HIDDEN_LEAVES``)
#: and precreated (``sandbox._CREW_PRECREATE_HIDDEN_DIR_LEAVES``), so this leaf inherits
#: the mask, the directory-``link(2)`` refusal and the precreation with no new registration
#: -- and, being introduced by THIS version's code, it never existed on any prior release,
#: so a pre-upgrade agent could not have seeded it. It is written ONLY by the gateway's
#: startup read path (:func:`is_declared`), never by any dashboard or CLI surface: a writer
#: an automated caller can reach is itself a grant, and this module stays writer-free in
#: that sense. Holds ``{"anchor": {...}, "grant": {...}, "pre_anchor": [...], "sig": "..."}``
#: -- see :func:`_read_provenance`.
_PROVENANCE_FILENAME: str = "provenance.json"

#: The gateway-only key that AUTHENTICATES the provenance record, a SIBLING of both
#: ``grant.json`` and ``provenance.json`` inside the same ``standing-approval/`` keystone
#: directory. This is the trust source the record's signature is computed under, and it is
#: the crux of the trusted-init defence, so its unforgeability is argued in full:
#:
#: * **Why NOT an existing gateway secret** (``token_signing.key``, ``sel_hmac.key``,
#:   ``.local_secret``). Each of those was persisted to disk MONTHS before it entered the
#:   sandbox mask sets (``token_signing.key`` since 2026-06-03, masked only ~2026-09-01),
#:   so on the very releases whose agent-writable ``standing-approval/`` this defence must
#:   survive, those keys sat UNMASKED and an agent subprocess could read their bytes. A
#:   pre-upgrade agent that exfiltrated any of them could forge a signature after upgrade.
#:   "A new leaf a pre-upgrade agent could not seed" is NOT the same as "a key a pre-upgrade
#:   agent could not have", and the finding is precisely that gap.
#:
#: * **Why THIS key is sound.** It is introduced by THIS version's code, so it never
#:   existed on any prior release -- a pre-upgrade agent had no bytes to sign with. The
#:   gateway GENERATES it fresh (``secrets.token_bytes``) on the first masked boot, at the
#:   same moment it lays the anchor, and NEVER adopts a key file it did not itself create:
#:   a ``provenance.key`` present at anchor time is a pre-seed, so the gateway OVERWRITES it
#:   with a fresh key via atomic replace rather than trusting the planted bytes. That closes
#:   the recursion a plain "new leaf" would leave open. Going forward, this version masks
#:   the whole ``standing-approval/`` directory out of every agent sandbox, so the agent
#:   cannot read the freshly-generated key to forge a signature either.
#:
#: * **Fail closed.** Verification requires a key that the gateway holds; when the key is
#:   absent, unreadable, short or the signature does not verify under it, the record is not
#:   trusted and the grant is refused. The key leaf inherits the directory mask, the
#:   ``link(2)`` refusal and the precreation with no new registration, exactly like the
#:   provenance record.
_PROVENANCE_KEY_FILENAME: str = "provenance.key"

#: Bytes of fresh entropy in the provenance signing key. 32 bytes is a full HMAC-SHA256
#: block's worth of key material and matches ``token_signing.key``'s ``_MIN_KEY_BYTES``.
_PROVENANCE_KEY_BYTES: int = 32


def _read_grant_bytes() -> tuple[bytes, int, int] | None:
    """Read the grant document, refusing a link, a non-regular file or a second name.

    A plain ``read_text()`` follows a symlink, which would undo this leaf's whole
    point. The keystone directory is bind-masked and pre-created, so an in-sandbox
    process cannot place the link itself -- but an operator who aliases the document
    onto a sandbox-visible path (a dotfile manager is the ordinary way that happens)
    would hand the agent a writable name for the file that authorizes it. The reader
    refuses that shape rather than resolving it, which is the same disposition
    ``sandbox`` takes for a ceiling it cannot cover under a second name.

    Defences, in order, following :func:`session_pid_sig._read_regular_nofollow`:

    * ``O_NOFOLLOW`` (POSIX): the open itself refuses a symlink final component,
      race-free where an ``is_symlink()`` pre-check is not.
    * ``lstat`` pre-check plus a post-open identity check, for the one platform with
      no ``O_NOFOLLOW``: a link planted between the two opens its TARGET, whose
      ``(st_dev, st_ino)`` cannot match the vetted regular file.
    * ``S_ISREG``: rejects a FIFO or device at that name. ``O_NONBLOCK`` is in the
      open flags for that case specifically: a blocking ``O_RDONLY`` open of a FIFO
      with no writer never returns, so without it a FIFO planted at this name would
      hang the gateway's boot instead of being rejected by the check below. The flag
      is ignored for a regular file, which is every legitimate case.
    * ``st_nlink == 1``: a second hard link is a second writable name for this very
      inode, and the mask covers a path rather than an inode -- the exact shape this
      keystone exists to deny, so the reader must not accept a document carrying one.
    * A size bound checked against both ``fstat`` and the bytes actually read.

    **Why the deltas are not folded into that shared reader.**
    :func:`session_pid_sig._read_regular_nofollow` is read by ``session_pid_sig`` and
    ``session_token_sig``, and neither delta here is neutral for them. ``st_nlink == 1``
    is a REFUSAL, so folding it in refuses a legitimately hard-linked pid or token
    signature -- the ordinary output of a snapshot or dotfile tool, and harmless for those
    two because their documents are not authorizations: a second name for one grants
    nothing, while a second name for THIS one is the whole attack. Making the refusal
    conditional adds a parameter whose meaning is "be an authorization reader", which is
    this function under another name. ``O_NONBLOCK`` could fold on its own and would close
    a FIFO hang on that helper's own agent-writable mapping directory, but that changes
    two other modules' startup behaviour and belongs with whoever owns them rather than
    riding in on this leaf.

    Returns ``(data, st_dev, st_ino)`` on success -- the bytes plus the TRUSTED identity
    of the vetted descriptor, so a caller can bind provenance to the very inode these
    bytes came from -- and ``None`` on any refusal or I/O error, so every caller fails
    closed. The descriptor's release counts as part of the read: a failing ``close`` is
    caught here and resolves to no grant, because this reader runs on the gateway's
    startup thread outside any ``try``, so an error escaping it would abort boot instead
    of withholding a grant.
    """
    path = standing_approval_path()
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    pre: os.stat_result | None = None
    try:
        if not nofollow:
            pre = os.lstat(path)
            if stat.S_ISLNK(pre.st_mode):
                logger.warning("standing auto-approve keystone is a symlink; refusing it")
                return None
        fd = os.open(path, os.O_RDONLY | nofollow | nonblock)
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning(
            "standing auto-approve keystone could not be opened as a plain file; "
            "treating approvals as required"
        )
        return None
    closed_cleanly = True
    grant_dev: int = -1
    grant_ino: int = -1
    try:
        st = os.fstat(fd)
        if pre is not None and (st.st_dev, st.st_ino) != (pre.st_dev, pre.st_ino):
            return None
        if not stat.S_ISREG(st.st_mode):
            logger.warning("standing auto-approve keystone is not a regular file; refusing it")
            return None
        if st.st_nlink != 1:
            logger.warning(
                "standing auto-approve keystone has %d hard links, so the grant is "
                "reachable under another name; refusing it",
                st.st_nlink,
            )
            return None
        if st.st_size > _MAX_GRANT_BYTES:
            return None
        grant_dev, grant_ino = st.st_dev, st.st_ino
        chunks: list[bytes] = []
        remaining = _MAX_GRANT_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError:
        return None
    finally:
        try:
            os.close(fd)
        except OSError:
            closed_cleanly = False
    if not closed_cleanly:
        logger.warning(
            "standing auto-approve keystone could not be closed; treating approvals as required"
        )
        return None
    data = b"".join(chunks)
    if len(data) > _MAX_GRANT_BYTES:
        return None
    return data, grant_dev, grant_ino


def _read_all() -> dict[str, Any]:
    """The whole document, or ``{}`` when it is missing, refused or unreadable.

    Failing soft is the right READ behaviour: an authorization record that cannot be
    parsed is not an authorization, so the grant stays withheld and the session
    prompts. An absent document, a refused shape and an unparseable one resolve
    identically, which is also what makes the empty mask a sandboxed reader would see
    equal to no grant.
    """
    return _read_grant_identity()[0]


def _read_grant_identity() -> tuple[dict[str, Any], int, int]:
    """The parsed grant document plus the TRUSTED on-disk identity it was read from.

    Returns ``(document, st_dev, st_ino)``. The document is ``{}`` and the identity is
    ``(-1, -1)`` when the leaf is missing, refused or unparseable, so every caller fails
    closed. The identity is the ``fstat`` of the vetted descriptor
    :func:`_read_grant_bytes` opened -- after its ``O_NOFOLLOW``, single-hard-link and
    regular-file checks -- so it names the very inode whose bytes produced the document
    and cannot be a symlink target or a second-name alias. Provenance binds to this
    identity rather than to ``granted_at`` alone, because ``granted_at`` is self-declared
    inside a pre-seedable document and a pre-upgrade agent writes any value it likes.
    """
    read = _read_grant_bytes()
    if read is None:
        return {}, -1, -1
    raw_bytes, dev, ino = read
    try:
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning(
            "standing auto-approve keystone is unreadable; treating approvals as required"
        )
        return {}, -1, -1
    return (raw if isinstance(raw, dict) else {}), dev, ino


def _keystone_is_masked(requested_mode: str) -> bool:
    """Whether the sandbox this host builds for an agent spawn covers the keystone.

    The document's authority rests on the mask, not on its own contents: an agent
    subprocess that can see the real data home creates the directory and writes the
    grant itself, and the next startup reads what that subprocess wrote as the
    operator's standing authority. So the grant counts only where the mask holding
    the leaf out of an agent's reach is actually in force.

    Two predicates in :mod:`sandbox` already answer when the mask is skipped, and each
    says in its own docstring that a control whose security argument depends on the mask
    must not carry a copy of that reasoning. This composes them rather than re-spelling
    them, so a future branch that skips the mask is a change to them and reaches this
    grant for free:

    * :func:`sandbox.credential_mask_applies` answers whether ``wrap_argv`` would thread
      the hidden-dir set through the backend it selects for *requested_mode*. It owns the
      clamp to the governed floor, the ``off`` tier, the no-backend host, and the process
      already running inside a Crew sandbox -- that last one matters here because the
      outer sandbox deliberately leaves ``~/.aws``, ``~/.ssh`` and ``~/.kube`` readable
      and is not a substitute for an adapter-specific mask.
    * :func:`sandbox.spawn_delegates_masking` answers the different question that predicate
      cannot: on Windows, and on macOS with kiro-cli's internal sandbox enabled, the spawn
      is handed to kiro-cli. A backend is present, so the first predicate answers True, and
      yet none of Crew's masks are applied and the leaf is reachable from inside that
      confinement.

    What is left for this function is the one question neither of them asks: whether the
    keystone actually sits inside a path the masked target set names.

    **The platform consequence, stated rather than left to be discovered.** Crew's mask
    is a Linux bind mount or a macOS Seatbelt profile, so the standing grant is
    available on Linux with a working user namespace and on macOS where Crew's own
    Seatbelt runs. On Windows it is never available: there is no Windows backend for
    ``credential_mask_applies`` to find and the spawn is delegated there, so both
    predicates refuse. On macOS with kiro-cli's internal sandbox enabled it is likewise
    unavailable, because that configuration is exactly the delegation. This narrows what
    the declaration can do rather than what the retired config key could do -- that key
    grants nothing on ANY platform, so no host keeps a standing grant it could not hold.

    *requested_mode* is REQUIRED, and is the ``agent.sandbox`` value the caller already
    resolved: a startup must judge the document it is acting on rather than re-read
    configuration that may have moved underneath it, and every caller holds the value
    already, so a no-argument form would only be a second way to read it. It is passed
    through unclamped, because the clamp to the governed floor belongs to
    ``credential_mask_applies`` and applying it twice here would be the copy this
    function exists without: a floor raising a requested ``off`` to a confined tier is a
    masked host and keeps granting, and that predicate is where that is decided.

    ``normpath``, never ``realpath``: the target set is built with ``normpath`` for the
    reason :func:`sandbox._relocated_crew_targets` records, so a link-resolving syscall
    here could disagree with the rules the sandbox will actually install.
    """
    try:
        if not sandbox.credential_mask_applies(requested_mode):
            return False
        if sandbox.spawn_delegates_masking():
            return False
        leaf = os.path.normpath(str(standing_approval_path()))
        for target in sandbox._crew_hidden_sandbox_targets():
            masked = os.path.normpath(target)
            if leaf == masked or leaf.startswith(masked + os.sep):
                return True
        return False
    except Exception:
        logger.warning(
            "standing auto-approve keystone mask could not be established; "
            "treating approvals as required",
            exc_info=True,
        )
        return False


def _provenance_path() -> Path:
    """The gateway-held provenance leaf, a sibling of ``grant.json`` in the keystone dir."""
    return standing_approval_path().parent / _PROVENANCE_FILENAME


def _provenance_key_path() -> Path:
    """The gateway-only provenance SIGNING key, a sibling in the keystone dir."""
    return standing_approval_path().parent / _PROVENANCE_KEY_FILENAME


def _read_provenance_key_bytes() -> bytes | None:
    """Read the provenance signing key with the SAME nofollow / single-link hardening.

    The key inherits the keystone directory's mask, ``link(2)`` refusal and precreation,
    so an in-sandbox process cannot open or plant it on THIS version. The reader still
    keeps the grant reader's discipline against an operator-planted alias: ``O_NOFOLLOW``
    (with an ``lstat`` pre-check + post-open identity check where absent), ``S_ISREG`` with
    ``O_NONBLOCK`` against a FIFO boot hang, ``st_nlink == 1``, and a size bound.

    Returns the key bytes when they read as a plain single-link regular file of at least
    :data:`_PROVENANCE_KEY_BYTES`, else ``None``. ``None`` means "no usable key", which
    makes :func:`_verify_provenance` fail closed -- an unauthenticated record is not
    trusted.
    """
    path = _provenance_key_path()
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    pre: os.stat_result | None = None
    try:
        if not nofollow:
            pre = os.lstat(path)
            if stat.S_ISLNK(pre.st_mode):
                logger.warning("standing auto-approve provenance key is a symlink; refusing it")
                return None
        fd = os.open(path, os.O_RDONLY | nofollow | nonblock)
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning(
            "standing auto-approve provenance key could not be opened as a plain file; "
            "treating approvals as required"
        )
        return None
    try:
        st = os.fstat(fd)
        if pre is not None and (st.st_dev, st.st_ino) != (pre.st_dev, pre.st_ino):
            return None
        if not stat.S_ISREG(st.st_mode):
            logger.warning(
                "standing auto-approve provenance key is not a regular file; refusing it"
            )
            return None
        if st.st_nlink != 1:
            logger.warning(
                "standing auto-approve provenance key has %d hard links; refusing it",
                st.st_nlink,
            )
            return None
        if st.st_size > _MAX_GRANT_BYTES:
            return None
        chunks: list[bytes] = []
        remaining = _MAX_GRANT_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError:
        return None
    finally:
        try:
            os.close(fd)
        except OSError:
            logger.warning(
                "standing auto-approve provenance key could not be closed; "
                "treating approvals as required"
            )
            return None
    data = b"".join(chunks)
    if len(data) < _PROVENANCE_KEY_BYTES:
        return None
    return data


def _establish_provenance_key() -> bytes | None:
    """Generate a FRESH gateway-only signing key, overwriting any pre-seeded one.

    Called ONLY on the anchor-writing branch (first masked boot with no valid signed
    provenance). The gateway never ADOPTS an on-disk key -- a ``provenance.key`` present at
    anchor time is a pre-seed planted while a prior release left the directory
    agent-writable, and trusting it would hand the attacker the very key that authenticates
    the record. So this always writes fresh entropy and swaps whatever is at the name OUT
    via :func:`kiro_crew.atomic_write.atomic_write` (owner-only, symlink-swapping replace).

    Returns the fresh key bytes, or ``None`` if the key could not be persisted (the anchor
    is then not signable and the boot refuses -- fail closed on the gateway startup thread).
    """
    fresh = secrets.token_bytes(_PROVENANCE_KEY_BYTES)
    try:
        atomic_write(
            _provenance_key_path(),
            fresh,
            restrict_to_owner=True,
        )
        return fresh
    except OSError:
        logger.warning(
            "standing auto-approve provenance key could not be written; "
            "the standing grant stays withheld until it can be recorded"
        )
        return None


def _provenance_signing_material(record: dict[str, Any]) -> bytes:
    """The exact bytes signed / verified: the record's meaningful fields, canonicalised.

    The signature covers the anchor establishment marker, the distrusted ``pre_anchor``
    list, and the bound ``grant`` identity -- everything a forger would need to alter to
    defeat the check. It deliberately EXCLUDES the ``sig`` field itself (a signature cannot
    cover itself) and is computed over a canonical (sorted-key, separator-fixed) JSON
    encoding so signing and verifying agree byte for byte regardless of dict ordering.
    """
    signed = {k: v for k, v in record.items() if k != "sig"}
    return json.dumps(signed, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sign_provenance(record: dict[str, Any], key: bytes) -> str:
    """HMAC-SHA256 the record's meaningful fields under the gateway-only key."""
    return hmac.new(key, _provenance_signing_material(record), hashlib.sha256).hexdigest()


def _verify_provenance(record: dict[str, Any]) -> bool:
    """Whether the record carries a signature that verifies under the gateway-only key.

    Fails closed: an absent key, an absent/non-string ``sig``, or a signature that does not
    match under a constant-time compare all resolve to ``False`` -- an unauthenticated
    record is not trusted, which is what refuses a pre-upgrade agent's forged
    ``provenance.json`` (it never had the gateway key to sign it, and any key it planted was
    overwritten at anchor time).
    """
    key = _read_provenance_key_bytes()
    if key is None:
        return False
    sig = record.get("sig")
    if not isinstance(sig, str):
        return False
    expected = _sign_provenance(record, key)
    return hmac.compare_digest(sig, expected)


def _read_provenance() -> dict[str, Any] | None:
    """Read the provenance record with the SAME hardening the grant reader uses.

    The provenance leaf lives in the same masked, precreated ``standing-approval/``
    directory, so an in-sandbox process cannot open or plant it. But the reader must not
    weaken the discipline the grant reader established: an operator who aliases the whole
    directory onto a sandbox-visible path with a dotfile manager, or a hard link to the
    record, is the same shape this keystone denies. So this mirrors
    :func:`_read_grant_bytes` -- ``O_NOFOLLOW`` (with an ``lstat`` pre-check and post-open
    identity check where the flag is absent), ``S_ISREG`` with ``O_NONBLOCK`` against a
    FIFO boot hang, ``st_nlink == 1``, and the size bound.

    Returns the parsed object, or ``None`` when the leaf is absent, refused or
    unparseable. ``None`` means "no anchor yet" to :func:`is_declared`, which is the
    fail-closed first-boot state.
    """
    path = _provenance_path()
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    pre: os.stat_result | None = None
    try:
        if not nofollow:
            pre = os.lstat(path)
            if stat.S_ISLNK(pre.st_mode):
                logger.warning("standing auto-approve provenance leaf is a symlink; refusing it")
                return None
        fd = os.open(path, os.O_RDONLY | nofollow | nonblock)
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning(
            "standing auto-approve provenance leaf could not be opened as a plain file; "
            "treating approvals as required"
        )
        return None
    try:
        st = os.fstat(fd)
        if pre is not None and (st.st_dev, st.st_ino) != (pre.st_dev, pre.st_ino):
            return None
        if not stat.S_ISREG(st.st_mode):
            logger.warning(
                "standing auto-approve provenance leaf is not a regular file; refusing it"
            )
            return None
        if st.st_nlink != 1:
            logger.warning(
                "standing auto-approve provenance leaf has %d hard links; refusing it",
                st.st_nlink,
            )
            return None
        if st.st_size > _MAX_GRANT_BYTES:
            return None
        chunks: list[bytes] = []
        remaining = _MAX_GRANT_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError:
        return None
    finally:
        try:
            os.close(fd)
        except OSError:
            logger.warning(
                "standing auto-approve provenance leaf could not be closed; "
                "treating approvals as required"
            )
            return None
    data = b"".join(chunks)
    if len(data) > _MAX_GRANT_BYTES:
        return None
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning(
            "standing auto-approve provenance leaf is unreadable; treating approvals as required"
        )
        return None
    return raw if isinstance(raw, dict) else None


def _write_provenance(record: dict[str, Any], key: bytes) -> bool:
    """Sign the provenance record under the gateway-only key and publish it atomically.

    Gateway startup path ONLY -- there is deliberately no other writer. The record is
    SIGNED (:func:`_sign_provenance`) under *key* before it is written, so a later read only
    trusts it if the same gateway-only key still verifies the signature. The destination is
    predictable and its parent (the keystone directory) is precreated on the trusted side,
    but a same-uid operator dotfile could pre-plant a symlink at the record's name, so the
    write goes through :func:`kiro_crew.atomic_write.atomic_write`, which stages a fresh temp
    and ``os.replace``s it -- swapping a planted symlink OUT rather than following it -- and
    ``restrict_to_owner`` keeps the file 0o600.

    Returns whether the write succeeded. A failure resolves to no grant at the call site
    (the record the next boot needs was not established), never an aborted boot: this runs
    on the gateway's startup thread.
    """
    signed = dict(record)
    signed["sig"] = _sign_provenance(record, key)
    try:
        atomic_write(
            _provenance_path(),
            json.dumps(signed, sort_keys=True),
            restrict_to_owner=True,
        )
        return True
    except OSError:
        logger.warning(
            "standing auto-approve provenance leaf could not be written; "
            "the standing grant stays withheld until it can be recorded"
        )
        return False


def _grant_identity(document: dict[str, Any], dev: int, ino: int) -> dict[str, Any]:
    """The trusted binding recorded for a grant: its vetted inode plus ``granted_at``.

    ``(st_dev, st_ino)`` is the identity of the exact vetted inode the document was read
    from -- not settable from inside a pre-seeded document -- and ``granted_at`` folds in
    the self-declared timestamp so that a RE-declaration (operator rewrites the grant with
    a new timestamp, which an atomic editor also gives a new inode) is a distinct
    establishment. The pair is what a later boot compares the on-disk grant against.
    """
    return {
        "st_dev": dev,
        "st_ino": ino,
        _GRANTED_AT_FIELD: document.get(_GRANTED_AT_FIELD),
    }


def _identity_matches(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Whether two recorded grant identities name the same establishment."""
    return (
        a.get("st_dev") == b.get("st_dev")
        and a.get("st_ino") == b.get("st_ino")
        and a.get(_GRANTED_AT_FIELD) == b.get(_GRANTED_AT_FIELD)
    )


def _grant_is_provenanced(
    requested_mode: str, document: dict[str, Any], dev: int, ino: int
) -> bool:
    """The trusted-init invariant: honour a grant ONLY on gateway-recorded provenance.

    Called with a document that already declares ``True`` and a mask already in force, so
    the gateway process -- the trusted principal -- is establishing this. The invariant it
    enforces:

        No standing grant is honoured unless the gateway itself recorded provenance for
        THIS grant document while the mask was in force AND after the version's anchor.

    That closes the pre-seed attack. On prior releases ``standing-approval/`` was unmasked
    and agent-writable, so a hostile pre-upgrade agent could write ``grant.json`` = ``True``
    and wait for an upgrade. It could ALSO create the directory and plant a forged
    ``provenance.json`` (an empty ``anchor`` dict + empty ``pre_anchor``) whose shape defeats
    a structure-only check -- the filename and record shape are public source. So the record
    is not merely a NEW leaf: it is AUTHENTICATED. Every trusted record carries an HMAC
    (``sig``) computed under :data:`_PROVENANCE_KEY_FILENAME`, a key the gateway generates
    fresh on the first masked boot and that a pre-upgrade agent provably could not have (see
    that constant's rationale). A record whose signature does not verify under the
    gateway-only key is treated as NO ANCHOR -- so a planted ``provenance.json`` cannot skip
    the anchor branch, and the pre-seed defence is not bypassable by pre-seeding the record
    too. The discriminator between an operator (who establishes the grant AFTER upgrading,
    with this version's mask in force) and a pre-seeded file is a SIGNED, matching
    gateway-held record that post-dates the anchor.

    The states, in order:

    * **No trusted (validly signed) provenance leaf yet (first masked boot of this
      version).** Generate a FRESH signing key, overwriting any pre-seeded key file, then
      write the ANCHOR SIGNED under it, capturing the identity of whatever grant is present
      RIGHT NOW as ``pre_anchor`` -- distrusted, because a file already on disk at anchor
      time predates this version's trust and may be the pre-seeded one. Refuse this boot and
      log the activation notice: the operator (re)writes the keystone now, and the next boot
      honours it. A ``provenance.json`` present but unsigned/forged lands here too, because
      its signature does not verify.
    * **Anchor exists, on-disk grant matches a ``pre_anchor`` identity.** REFUSE: this is
      the grant that was already present before the anchor -- the pre-seed. It never
      becomes trusted; the operator must re-establish it (a rewrite gives a new inode /
      ``granted_at``, so it stops matching ``pre_anchor``).
    * **Anchor exists, on-disk grant matches the recorded ``grant`` provenance.** HONOUR:
      the gateway already recorded this exact establishment after the anchor.
    * **Anchor exists, grant is neither pre-anchor nor already recorded.** The operator
      established it after the anchor. Record its provenance now and HONOUR.

    A file present only at first-upgrade boot can never satisfy this: it is captured as
    ``pre_anchor`` and stays refused until re-established with the mask in force. Trust
    rests on the gateway-held, SIGNED leaf gated by :func:`_keystone_is_masked` and by
    :func:`_verify_provenance`, never on the grant's own mtime (agent-settable via ``utime``
    on a prior release) or ``granted_at`` (agent-writable inside the pre-seedable document),
    and never on an unauthenticated on-disk ``anchor`` object.
    """
    identity = _grant_identity(document, dev, ino)
    provenance = _read_provenance()

    if (
        provenance is None
        or not isinstance(provenance.get("anchor"), dict)
        or not _verify_provenance(provenance)
    ):
        # First masked boot of this version (or an unsigned/forged record that does not
        # verify): generate a fresh gateway-only key, establish the anchor SIGNED under it,
        # distrust whatever is present now, and refuse until the operator re-establishes the
        # grant. Generating the key overwrites any pre-seeded key file, so a planted key can
        # never be the one the record is trusted under.
        key = _establish_provenance_key()
        if key is None:
            return False
        anchor_record = {
            "anchor": {"established_at": datetime.now(timezone.utc).isoformat()},
            "pre_anchor": [identity],
            "grant": None,
        }
        if _write_provenance(anchor_record, key):
            logger.warning(
                "standing auto-approve is declared on the keystone, but this version has "
                "no trusted provenance record for it yet (first boot with the keystone "
                "mask in force). The grant is NOT honoured this boot. To activate it, "
                "(re)write %s by hand now -- on this version, with the mask in force -- "
                "and the next startup will record its provenance and honour it. This "
                "refuses a grant document (or a forged provenance record) that a "
                "pre-upgrade agent may have planted while the leaf was unmasked.",
                standing_approval_path(),
            )
        return False

    # The record verified under the gateway-only key, so its key is trusted; reuse it to
    # sign any grant provenance recorded below.
    key = _read_provenance_key_bytes()
    if key is None:
        return False

    pre_anchor = provenance.get("pre_anchor")
    if isinstance(pre_anchor, list):
        for stale in pre_anchor:
            if isinstance(stale, dict) and _identity_matches(identity, stale):
                logger.warning(
                    "standing auto-approve is declared, but this grant document was "
                    "already present at this version's first masked boot, so it is NOT "
                    "trusted (it may have been planted by a pre-upgrade agent). To "
                    "activate the grant, re-create %s by hand now on this version; the "
                    "next startup will record its provenance and honour it.",
                    standing_approval_path(),
                )
                return False

    recorded = provenance.get("grant")
    if isinstance(recorded, dict) and _identity_matches(identity, recorded):
        return True

    # Established after the anchor and not pre-seeded: record provenance (signed) and honour.
    updated = dict(provenance)
    updated.pop("sig", None)
    updated["grant"] = identity
    if _write_provenance(updated, key):
        return True
    return False


def is_declared(requested_mode: str) -> bool:
    """Whether the operator has declared a STANDING skip of every tool approval.

    Fails closed to ``False`` on a missing, unreadable or malformed document, which
    is what the issue's "an absent or unreadable leaf resolves to refusal" asks for.

    Only a real ``bool`` ``True`` counts. A truthy string or number does not, for the
    reason ``config.sections._read_skip_permissions`` gives about the key it replaces:
    ``"false"``, ``"0"`` and ``"no"`` are all truthy in Python, so a bare ``bool(...)``
    here would read an explicit disable as the standing grant. LOCAL only -- no network.

    A declared document is honoured only where :func:`_keystone_is_masked` holds, so an
    agent subprocess that can reach the leaf cannot author its own standing authority.
    The mask is established only once the document already grants, so a host with no
    grant -- every default install -- pays nothing for the check and stays silent;
    a host that HAS one and cannot mask it says so, because an operator who wrote the
    document and still sees prompts needs the reason.

    Even where the mask holds, disk CONTENT alone does not grant. On prior releases the
    keystone directory was unmasked and agent-writable, so a hostile pre-upgrade agent
    could plant ``grant.json`` = ``True`` and wait for an upgrade to read it back as the
    operator's standing authority. So a declared, masked grant is honoured only when the
    gateway itself recorded TRUSTED PROVENANCE for that exact grant document -- bound to
    its vetted inode identity and ``granted_at`` -- while the mask was in force and after
    this version's anchor (:func:`_grant_is_provenanced`). A document present at first
    upgrade boot has no such record and is refused; the operator re-establishes it on this
    version and the next boot honours it.

    *requested_mode* is forwarded to those predicates and is required: pass the
    ``agent.sandbox`` value already in hand.
    """
    document, dev, ino = _read_grant_identity()
    if document.get(GRANT_FIELD) is not True:
        return False
    if not _keystone_is_masked(requested_mode):
        logger.warning(
            "standing auto-approve is declared on the keystone but is UNAVAILABLE on "
            "this host: Kiro Crew's own sandbox does not mask that leaf away from the "
            "agent here, so the document is not an authorization. Approvals are required"
        )
        return False
    return _grant_is_provenanced(requested_mode, document, dev, ino)


def migration_notice(
    requested_mode: str,
    *,
    windows: bool | None = None,
    masked: bool | None = None,
) -> str:
    """The words a startup logs when a retired ``config.json`` declaration is stranded.

    Returned rather than logged here so the two startup paths that establish the
    declared grant (the dashboard's and Slack's) word it identically, and so a test
    can assert on the text an operator actually sees instead of on a log call.

    The whole value of this notice is that the person reading it can act on it without
    going to find the documentation first, so it names the resolved path and the one
    line to put in it. It deliberately does NOT hand over a runnable redirection
    command on any platform:

    * It does not spell the path as ``$KIROCREW_HOME/...``. That variable is unset on a
      default installation, where the data home comes from ``config_dir()``, so a shell
      expands the env-var form to ``/standing-approval`` and a command using it fails at
      the filesystem root.
    * It does not hand a Windows operator a POSIX one-liner. ``mkdir -p`` and ``printf``
      are not commands there, and POSIX single quotes are not quoting characters to
      ``cmd`` at all -- the same trap :func:`sandbox._delete_file_command` records.
    * It does not hand a POSIX operator a ``printf ... > path`` redirection either. A
      pre-upgrade agent can plant a symlink at the keystone path (the crew data-home
      root is agent-writable), and an operator who copy-pastes such a line would follow
      that symlink and overwrite the attacker's chosen target with operator privilege.
      So BOTH renderings name the path and the exact one line and ask the operator to
      create the file by hand -- no redirection this notice emits can be turned against
      them.

    *windows* is retained as a PARAMETER so a test can exercise the wording from one
    platform; the remedy text is now identical on both, so it no longer changes the
    output, but every platform defect in this area came from a string written once and
    verified only where it was written, so the parameter stays. *masked* is a parameter
    for the same reason, and *requested_mode* -- required, like :func:`is_declared`'s --
    is what resolves it when *masked* is not given.

    **There are two renderings, and which one is right depends on the mask.** Where
    :func:`_keystone_is_masked` holds, writing the document restores the grant and the
    notice hands over the path and line to write. Where it does not, writing the document
    restores NOTHING -- :func:`is_declared` refuses a declaration whose mask is absent --
    so a remedy there would send the operator to create a file, watch the prompts
    continue, and have nothing further to read. That rendering states the unavailability
    and points at the ad-hoc duration, which is the grant that does work on such a host.
    It is also the correct text when the keystone is ALREADY written on an unmasked host,
    which is the case where a remedy reads most absurdly.
    """
    del windows  # retained for signature/back-compat; the remedy no longer branches on it
    on_masked = _keystone_is_masked(requested_mode) if masked is None else masked
    path = standing_approval_path()
    document = f'{{"{GRANT_FIELD}": true}}'
    if not on_masked:
        return (
            "agent.dangerously_skip_permissions is set in config.json but that key no "
            "longer grants anything, and the standing auto-approve declaration it moved "
            f"to ({path}) is UNAVAILABLE on this host: the declaration is honoured only "
            "where Kiro Crew's own sandbox masks that leaf away from the agent, and this "
            "host's agent spawn runs without that mask. Writing the document would grant "
            "nothing. Approvals are REQUIRED here; enable auto-approve ad hoc instead "
            "(it lasts agent.yolo_duration), and remove the retired key from config.json."
        )
    preamble = (
        "agent.dangerously_skip_permissions is set in config.json but that key no "
        "longer grants anything: the standing auto-approve declaration moved to the "
        f"operator-owned keystone {path}, which an agent sandbox cannot open. "
        "Approvals are REQUIRED until you write it. "
    )
    # Deliberately NOT a runnable redirection command. A pre-upgrade agent can plant a
    # symlink at ``path`` (the crew data-home root is agent-writable), and an operator who
    # copy-pastes a ``printf ... > path`` line would follow that symlink and overwrite the
    # attacker's chosen target with operator privilege. The notice names the path and the
    # exact one-line content and asks the operator to create the file by hand, so no
    # redirection this notice emits can be turned against them.
    #
    # It also spells the TRUSTED-INIT activation sequence, because a single "write it and
    # restart" is wrong under the provenance defence: the first restart after the file
    # appears only lays this version's anchor and STILL refuses the grant (a document
    # already on disk at anchor time is distrusted as possibly pre-seeded). Activation is
    # RESTART -> REWRITE -> SECOND RESTART, so an operator who is not told this sees the
    # first restart keep prompting and concludes the keystone does not work.
    remedy = (
        f"To restore the grant, create the directory {path.parent} if it does not exist, "
        f"then create the file {path} by hand containing exactly this one line: "
        f"{document} . Activation then takes two restarts, by design: this version records "
        "trusted provenance for a declaration only AFTER it has laid its own anchor, so "
        "the FIRST restart after you create the file lays that anchor and still requires "
        "approvals (a file already present at that first boot is distrusted as one a "
        "pre-upgrade agent may have planted). Then REPLACE the file with a fresh one -- "
        "delete it and create it again containing the same one line -- and restart a "
        "SECOND time. The replacement matters: the provenance binds to the inode "
        "identity of that file and its granted_at, and an edit in place preserves BOTH, so the second "
        "restart would still see a document present at the previous boot and refuse it. A "
        "delete-and-recreate gives it a distinct identity established after the anchor, "
        "which that restart records and honours, and it stays honoured thereafter.  "
    )
    return preamble + remedy + "(then remove the retired key from config.json)."
