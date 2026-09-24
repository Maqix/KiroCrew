"""The standing auto-approve switch lives on an UNOPENABLE keystone, not in config.json.

A read-only seal on ``config.json`` closes a write to the sealed NAME. It cannot close
the inode behind that name: the crew data-home root is writable in every sandbox, the
agent runs as the operator's uid and so OWNS the document, ``link(2)`` needs no write
permission on the file it names a second time, and a bind mount seals a MOUNT rather
than an inode. ``sandbox`` states that fact itself where it refuses a governance
ceiling whose ``st_nlink`` is not 1.

The switch therefore lives on a leaf a sandboxed process cannot OPEN. Two properties
carry that, and each gets its own assertion below because either alone is
insufficient:

* masked rather than sealed read-only -- a sealed-but-readable leaf is still a
  ``link(2)`` source;
* a DIRECTORY rather than a file -- Linux refuses ``link(2)`` on a directory outright,
  so the alias shape has no source even in principle.

Every claim has a discriminating control beside it, so a vacuous or misspelled set
cannot make this file pass.
"""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys

import pytest

from kiro_crew import platform_compat, sandbox, standing_approval
from kiro_crew.config import loader
from kiro_crew.security import paths as security_paths
from kiro_crew.subprocess_utf8 import UTF8_TEXT

#: The genuine predicate, captured before the module fixture pins it away, so the
#: platform class can put it back and exercise the real branches.
_REAL_SPAWN_DELEGATES_MASKING = sandbox.spawn_delegates_masking


@pytest.fixture(autouse=True)
def maskable_host(monkeypatch):
    """Pin the host as one whose sandbox CAN mask the keystone.

    A grant is honoured only where the mask holding the leaf out of an agent's reach is
    in force, so every case here that asserts a document grants needs that precondition
    stated rather than inherited from whatever the test machine happens to support. The
    cases about the mask itself state their own departure from this.

    Delegation is pinned for the same reason and not as a convenience: the mask is a Linux
    bind mount or a macOS Seatbelt profile, so on a Windows runner every one of those
    assertions would be reading the platform answer instead of the behaviour it names.
    The DELEGATION PREDICATE is what gets pinned rather than ``sys.platform``, so that the
    real host still decides every other platform branch -- notably ``migration_notice``'s
    own rendering default, whose test would go vacuous against a forced platform.
    ``TestTheMaskIsPlatformBound`` is where the platform answer is the subject, and it
    restores the real predicate.

    The in-sandbox marker is cleared for the third precondition:
    ``credential_mask_applies`` refuses a process already inside a Crew sandbox, so a
    suite run from inside one would read every grant case as unmasked.
    """
    monkeypatch.setattr(sandbox, "_governance_sandbox_floor", lambda: None)
    monkeypatch.setattr(sandbox, "detect_backend", lambda config_mode="auto": "namespace")
    monkeypatch.setattr(sandbox, "spawn_delegates_masking", lambda: False)
    monkeypatch.delenv(sandbox._IN_SANDBOX_MARKER, raising=False)


@pytest.fixture()
def crew_home(tmp_path, monkeypatch):
    """Point every reader of the data home at a scratch tree."""
    home = tmp_path / ".kiro" / "crew"
    home.mkdir(parents=True)
    monkeypatch.setattr(loader, "config_dir", lambda: home)
    monkeypatch.setattr(sandbox, "config_dir", lambda: home)
    return home


def _write_grant(home, document: object) -> None:
    leaf = home / loader.STANDING_APPROVAL_DIRNAME
    leaf.mkdir(parents=True, exist_ok=True)
    (leaf / loader.STANDING_APPROVAL_FILENAME).write_text(
        document if isinstance(document, str) else json.dumps(document),
        encoding="utf-8",
    )


def _activate_grant(document: object = None, *, mode: str = "auto") -> None:
    """Establish trusted provenance for the grant, as an operator would after upgrade.

    Since this PR, a grant document is honoured only once the GATEWAY has recorded
    provenance for it while the mask was in force AND after this version's anchor. That
    is a two-boot dance: the first masked boot writes the anchor and refuses (a file
    present then is distrusted as possibly pre-seeded); the operator (re)writes the
    keystone; the next boot records the provenance and honours it.

    Most tests here only care that a LEGITIMATE operator grant is honoured, not about the
    activation mechanics, so this collapses the dance: it drives one refusing boot to lay
    the anchor, then (re)writes the document with a fresh inode so it post-dates the
    anchor, so a following :func:`is_declared` records provenance and grants. ``crew_home``
    fixtures point the readers at a scratch tree, so this operates on that tree.

    Pass *document* to (re)write the grant with specific content; omit it to leave the
    current on-disk document in place (used when the grant is already written).
    """
    # First masked boot: lays the anchor and captures whatever is present as pre-anchor.
    standing_approval.is_declared(mode)
    # The operator (re)writes the keystone AFTER the anchor. A fresh write gives a new
    # inode AND (by default) a distinct granted_at, so the identity leaves the distrusted
    # pre_anchor set regardless of whether the filesystem recycled the inode -- which keeps
    # this helper FS-agnostic on CI runners that recycle inodes on unlink+create.
    if document is not None:
        _rewrite_grant(document)
    else:
        _rewrite_grant({standing_approval.GRANT_FIELD: True, "granted_at": "2026-09-28T00:00:00Z"})


def _rewrite_grant(document: object) -> None:
    """Replace the grant document so it gets a NEW inode (the operator's re-write)."""
    path = loader.standing_approval_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".new")
    tmp.write_text(
        document if isinstance(document, str) else json.dumps(document),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _write_signed_provenance(record: dict) -> None:
    """Write a provenance record SIGNED under the gateway key currently on disk.

    Since the trusted-init change a provenance record is trusted only if its ``sig``
    verifies under the gateway-only key. A test that manipulates the record on disk (to
    exercise the mismatch / recorded-grant paths) must re-sign it, or the reader treats it
    as a forged record. This mirrors :func:`standing_approval._write_provenance`'s signing.
    """
    key = standing_approval._read_provenance_key_bytes()
    assert key is not None, "provenance key must exist (drive one refusing boot first)"
    signed = dict(record)
    signed.pop("sig", None)
    signed["sig"] = standing_approval._sign_provenance(record, key)
    standing_approval._provenance_path().write_text(json.dumps(signed, sort_keys=True))


class TestKeystonePlacement:
    """Where the leaf sits in the two fences, and that the two agree on its name."""

    def test_the_leaf_is_on_the_agent_file_tool_keystone_floor(self):
        assert loader.STANDING_APPROVAL_DIRNAME in security_paths._CREW_SECRET_LEAVES
        # Discriminating control: an ordinary crew-home leaf is NOT on the floor, so a
        # pass above cannot come from a set that swallows every name.
        assert "config.json" not in security_paths._CREW_SECRET_LEAVES

    def test_the_leaf_is_masked_not_merely_sealed_read_only(self):
        """The whole point of the move: unopenable in-sandbox, not write-denied.

        A read-only seal leaves the document READABLE, and a readable inode the caller
        owns is a ``link(2)`` source. Being in the readonly set instead of the hidden
        one would reproduce exactly the residual this leaf exists to close.
        """
        assert loader.STANDING_APPROVAL_DIRNAME in sandbox._CREW_HIDDEN_LEAVES
        assert loader.STANDING_APPROVAL_DIRNAME not in sandbox._CREW_READONLY_LEAVES
        # Control: the sealed-but-readable disposition really is a different set with
        # real members, so the second assertion is not vacuously true.
        assert "computer_use.json" in sandbox._CREW_READONLY_LEAVES
        assert "computer_use.json" not in sandbox._CREW_HIDDEN_LEAVES

    def test_the_leaf_is_precreated_so_the_isdir_guarded_mask_is_not_vacuous(self):
        """An absent name gets NO bind, and absent is the default on every install.

        ``mount(2)`` cannot mask a path that does not exist and the mask loop guards on
        ``isdir``, so without pre-creation a sandboxed process could CREATE the
        directory in the writable data-home root and write the grant the gateway reads
        back as the operator's own standing authority.
        """
        assert loader.STANDING_APPROVAL_DIRNAME in sandbox._CREW_PRECREATE_HIDDEN_DIR_LEAVES

    def test_the_mask_and_the_reader_name_the_same_directory(self):
        """``sandbox`` spells the leaf as a literal to stay off the config-loader import
        chain, exactly as it does for the live-target pointer. This is the pin that
        keeps the two spellings from drifting into two different directories -- which
        would mask one name while the gateway read another.
        """
        assert sandbox._STANDING_APPROVAL_LEAF == loader.STANDING_APPROVAL_DIRNAME

    def test_the_grant_document_lives_inside_the_classified_directory(self, crew_home):
        """A directory entry covers every child; a file entry would leave the container
        writable, which is the same hole one level up.
        """
        path = loader.standing_approval_path()
        assert path.parent.name == loader.STANDING_APPROVAL_DIRNAME
        assert path.name == loader.STANDING_APPROVAL_FILENAME
        assert path.parent.parent == crew_home

    def test_a_hidden_leaf_needs_no_child_readable_classification(self):
        """``test_sandbox_governance_mask`` pins the child-readable/withheld pair
        complete and disjoint over ``_CREW_SANDBOX_VISIBLE_LEAVES |
        _CREW_READONLY_LEAVES``. A hidden leaf is in neither source, so it is correctly
        absent from both halves rather than unclassified.
        """
        union = set(sandbox._CREW_SANDBOX_VISIBLE_LEAVES) | set(sandbox._CREW_READONLY_LEAVES)
        assert loader.STANDING_APPROVAL_DIRNAME not in union
        assert loader.STANDING_APPROVAL_DIRNAME not in sandbox._CREW_CHILD_READABLE_LEAVES
        assert loader.STANDING_APPROVAL_DIRNAME not in sandbox._CREW_CHILD_WITHHELD_LEAVES


class TestPrecreateIsBehavioural:
    """Membership in the precreate list is not the property; creation is."""

    def test_materialise_creates_the_directory_owner_only(self, crew_home):
        target = crew_home / loader.STANDING_APPROVAL_DIRNAME
        assert not target.exists()  # fresh install: nothing declared yet

        created = sandbox._materialize_maskable_dirs()

        assert target.is_dir()
        assert str(target) in created
        if os.name == "posix":
            # Owner-only whatever the umask: no group or other access.
            assert (target.stat().st_mode & 0o077) == 0

    def test_an_empty_directory_is_the_readers_absent_equivalent(self, crew_home):
        """What a sandboxed reader would see through the mask must mean NO GRANT.

        This is the criterion the precreate lists require of every leaf they
        materialise, and for a GRANT the empty form is the safe direction.
        """
        sandbox._materialize_maskable_dirs()
        assert (crew_home / loader.STANDING_APPROVAL_DIRNAME).is_dir()
        assert standing_approval.is_declared("auto") is False


class TestDirectoryHasNoAliasSource:
    """The property that makes a directory close the hole rather than move it."""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX link(2) semantics")
    def test_the_kernel_refuses_a_second_name_for_a_directory(self, crew_home):
        """``link(2)`` on a directory is EPERM, so the shape the issue reports for a
        sealed FILE has no source here. Run against the real keystone directory rather
        than an arbitrary one, so the assertion is about this leaf.
        """
        sandbox._materialize_maskable_dirs()
        target = crew_home / loader.STANDING_APPROVAL_DIRNAME
        alias = crew_home / "alias-attempt"

        with pytest.raises(OSError) as err:
            os.link(str(target), str(alias), follow_symlinks=False)

        assert not alias.exists()
        # Control: the same call on a regular FILE in the same directory SUCCEEDS --
        # which is the residual for a sealed file leaf, and why this leaf is a
        # directory.
        regular = crew_home / "ordinary.json"
        regular.write_text("{}", encoding="utf-8")
        regular.chmod(0o444)
        file_alias = crew_home / "ordinary-alias.json"
        os.link(str(regular), str(file_alias))
        assert file_alias.stat().st_ino == regular.stat().st_ino
        assert err.value.errno != 0


class TestReadsFailClosed:
    """An absent or unreadable leaf resolves to refusal -- the issue's done-when."""

    def test_absent_leaf_is_no_grant(self, crew_home):
        assert standing_approval.is_declared("auto") is False

    def test_absent_document_inside_an_existing_directory_is_no_grant(self, crew_home):
        (crew_home / loader.STANDING_APPROVAL_DIRNAME).mkdir()
        assert standing_approval.is_declared("auto") is False

    def test_unparseable_document_is_no_grant(self, crew_home):
        _write_grant(crew_home, "{not json")
        assert standing_approval.is_declared("auto") is False

    def test_a_json_non_object_is_no_grant(self, crew_home):
        _write_grant(crew_home, [True])
        assert standing_approval.is_declared("auto") is False

    @pytest.mark.parametrize("value", ["true", "false", "0", "no", 1, 0, [], {}, None])
    def test_only_a_real_boolean_true_grants(self, crew_home, value):
        """A truthy STRING must not grant. ``"false"`` and ``"0"`` are truthy in
        Python, so a bare ``bool(...)`` here would read an explicit disable as the
        standing grant -- the same trap ``_read_skip_permissions`` documents.
        """
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: value})
        assert standing_approval.is_declared("auto") is False

    def test_an_explicit_boolean_true_grants(self, crew_home):
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        _activate_grant()  # gateway records trusted provenance for the operator's grant
        assert standing_approval.is_declared("auto") is True

    def test_an_explicit_boolean_false_does_not_grant(self, crew_home):
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: False})
        assert standing_approval.is_declared("auto") is False

    def test_an_unreadable_document_is_no_grant(self, crew_home):
        if os.name != "posix" or os.geteuid() == 0:
            pytest.skip("needs POSIX permission bits and a non-root uid")
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        _activate_grant()  # record provenance while the document is still readable
        path = loader.standing_approval_path()
        path.chmod(0o000)
        try:
            assert standing_approval.is_declared("auto") is False
        finally:
            path.chmod(0o600)
        # Control: the very same document reads as a grant once it is readable again,
        # so the refusal above came from the permission and not from the content.
        assert standing_approval.is_declared("auto") is True

    def test_a_close_failure_is_no_grant(self, crew_home, monkeypatch):
        """A failing ``close`` resolves to no grant instead of escaping the reader.

        The reader runs on the gateway's startup thread outside any ``try``, so an
        ``OSError`` leaving it aborts boot. Withholding the grant is the fail-closed
        answer, and it is the whole point of catching a call whose only job is to
        release a descriptor the read is already finished with.
        """
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        _activate_grant()  # record provenance so the control below actually grants
        # Control: the document grants while ``close`` behaves, so the refusal below
        # comes from the close failure and not from the content.
        assert standing_approval.is_declared("auto") is True

        real_close = os.close

        def failing_close(fd: int) -> None:
            real_close(fd)
            raise OSError(errno.EIO, "simulated close failure")

        # Patched for exactly one call: ``standing_approval.os`` is the stdlib module,
        # so the substitution is visible to every caller while it stands.
        with monkeypatch.context() as patched:
            patched.setattr(standing_approval.os, "close", failing_close)
            assert standing_approval.is_declared("auto") is False


class TestTheReaderRefusesAnAliasedGrant:
    """The reader must not resolve the alias shapes this keystone exists to deny.

    The mask and the directory stop an in-sandbox process from PLACING a link, but a
    reader that follows one would hand back a grant from wherever it points. An
    operator aliasing the document onto a sandbox-visible path with a dotfile manager
    is the ordinary way that happens, so the refusal belongs in the reader too --
    the same disposition ``sandbox`` takes for a ceiling it cannot cover under a
    second name.
    """

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
    def test_a_symlinked_grant_is_refused(self, crew_home):
        elsewhere = crew_home.parent / "aliased-grant.json"
        elsewhere.write_text(json.dumps({standing_approval.GRANT_FIELD: True}), encoding="utf-8")
        leaf = crew_home / loader.STANDING_APPROVAL_DIRNAME
        leaf.mkdir(parents=True)
        (leaf / loader.STANDING_APPROVAL_FILENAME).symlink_to(elsewhere)

        assert standing_approval.is_declared("auto") is False
        # Control: the identical document at a REAL path does grant, so the refusal
        # above came from the link and not from the content.
        (leaf / loader.STANDING_APPROVAL_FILENAME).unlink()
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        _activate_grant()
        assert standing_approval.is_declared("auto") is True

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX hard-link semantics")
    def test_a_grant_with_a_second_hard_link_is_refused(self, crew_home):
        """A second name for this inode is the shape the whole keystone denies.

        The mask covers a path rather than an inode, so a document reachable under
        another name is writable through that name whatever the mask says.
        """
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        _activate_grant()  # record provenance for the sole-link document
        path = loader.standing_approval_path()
        assert standing_approval.is_declared("auto") is True  # sole link: granted

        alias = crew_home / "second-name.json"
        os.link(str(path), str(alias))
        assert path.stat().st_nlink == 2
        assert standing_approval.is_declared("auto") is False

        # Breaking the extra link restores the grant, so the refusal tracked the
        # link count rather than anything else about the file.
        alias.unlink()
        assert path.stat().st_nlink == 1
        assert standing_approval.is_declared("auto") is True

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX FIFO semantics")
    def test_a_non_regular_grant_is_refused(self, crew_home):
        leaf = crew_home / loader.STANDING_APPROVAL_DIRNAME
        leaf.mkdir(parents=True)
        os.mkfifo(str(leaf / loader.STANDING_APPROVAL_FILENAME))
        assert standing_approval.is_declared("auto") is False

    def test_an_oversized_grant_is_refused(self, crew_home):
        """A huge file at this name must not be buffered into the gateway on boot."""
        padding = "x" * (standing_approval._MAX_GRANT_BYTES + 100)
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True, "pad": padding})
        assert loader.standing_approval_path().stat().st_size > standing_approval._MAX_GRANT_BYTES
        assert standing_approval.is_declared("auto") is False


class TestTheMigrationCommandRuns:
    """The notice's whole value is that the operator can act on it.

    Both renderings are asserted from THIS platform via the ``windows`` parameter,
    because the defect these tests exist for is a string verified only where it was
    written: the POSIX one-liner reached CI and failed on Windows, where ``mkdir -p``
    and ``printf`` are not commands and POSIX quotes are not quoting characters.

    The notice is deliberately NOT a runnable redirection command on any platform. A
    ``printf ... > path`` line an operator copy-pastes follows a symlink a pre-upgrade
    agent can plant at ``path`` (the crew data-home root is agent-writable), overwriting
    an attacker-chosen target with operator privilege. So both renderings name the path
    and the exact one line and ask the operator to create the file by hand.
    """

    def test_the_command_uses_the_resolved_path_not_an_env_var(self, crew_home):
        for on_windows in (False, True):
            notice = standing_approval.migration_notice("auto", windows=on_windows)
            # $KIROCREW_HOME is UNSET on a default installation, where the data home
            # comes from config_dir() -- an env-var form expands to /standing-approval
            # and fails at the filesystem root.
            assert "$KIROCREW_HOME" not in notice
            assert str(loader.standing_approval_path()) in notice

    def test_the_windows_rendering_uses_no_posix_shell_builtin(self, crew_home):
        """No single spelling writes this JSON in both cmd and PowerShell, so the
        Windows text states the path and the line rather than pretending to run.
        """
        notice = standing_approval.migration_notice("auto", windows=True)
        assert "mkdir -p" not in notice
        assert "printf" not in notice
        assert "'" not in notice.split("this one line:")[1]
        assert f'{{"{standing_approval.GRANT_FIELD}": true}}' in notice

    def test_the_posix_rendering_is_not_a_runnable_redirection(self, crew_home):
        """The POSIX text must NOT hand the operator a ``printf ... > path`` line: a
        copy-pasted redirection follows a symlink a pre-upgrade agent can plant at the
        keystone path, overwriting an attacker-chosen target with operator privilege.
        It names the path and the line for manual creation, like the Windows text.
        """
        notice = standing_approval.migration_notice("auto", windows=False)
        assert "printf" not in notice
        assert ">" not in notice
        assert "create the file" in notice
        assert f'{{"{standing_approval.GRANT_FIELD}": true}}' in notice

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX path creation")
    def test_creating_the_named_file_by_hand_produces_a_document_that_grants(self, crew_home):
        """End to end: the notice names the path and the exact line; creating that file
        by hand (as the operator is told to) yields a document that grants.

        Deliberately does NOT extract and execute a shell command from the notice: the
        notice emits none, precisely so a copy-pasted redirection cannot follow a
        planted symlink. This asserts the operator's manual action grants.
        """
        notice = standing_approval.migration_notice("auto", windows=False)
        assert standing_approval.is_declared("auto") is False
        # The one line the notice tells the operator to put in the file.
        document = f'{{"{standing_approval.GRANT_FIELD}": true}}'
        assert document in notice
        path = loader.standing_approval_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document + "\n", encoding="utf-8")

        assert path.is_file()
        # Since the trusted-init change, creating the file by hand grants only once the
        # gateway has recorded provenance for it on this version with the mask in force
        # (the notice tells the operator the next boot activates it). Drive that boot.
        _activate_grant(document + "\n")
        assert standing_approval.is_declared("auto") is True

    def test_the_default_rendering_follows_the_host(self, crew_home):
        """Omitting the parameter must not silently pick one platform's text."""
        assert standing_approval.migration_notice("auto") == standing_approval.migration_notice(
            "auto", windows=platform_compat.IS_WINDOWS
        )


class TestConfigKeyNoLongerGrants:
    """The migration is explicit: the retired key grants nothing and says so."""

    def test_the_retired_key_is_not_read_by_the_keystone_reader(self, crew_home):
        """The reader must not consult ``config.json`` at all. Writing the retired
        declaration into a config document next to an absent keystone must leave the
        answer at no-grant.
        """
        (crew_home / "config.json").write_text(
            json.dumps({"agent": {"dangerously_skip_permissions": True}}), encoding="utf-8"
        )
        assert standing_approval.is_declared("auto") is False

    def test_the_migration_notice_names_the_path_and_the_document(self):
        notice = standing_approval.migration_notice("auto")
        assert loader.STANDING_APPROVAL_DIRNAME in notice
        assert loader.STANDING_APPROVAL_FILENAME in notice
        assert "dangerously_skip_permissions" in notice
        # Actionable for a reader with no context: it must say the old key grants
        # nothing, not merely that something moved.
        assert "no longer grants" in notice

    def test_the_notice_is_ascii_so_every_log_sink_renders_it(self):
        standing_approval.migration_notice("auto").encode("ascii")


class TestStartupHonoursOnlyTheKeystone:
    """The gateway startup path reads the keystone, and announces a stranded key."""

    def _cfg(self, declared: bool):
        class _Agent:
            dangerously_skip_permissions = declared
            sandbox = "auto"

        class _Cfg:
            agent = _Agent()

        return _Cfg()

    @pytest.fixture()
    def startup(self, monkeypatch, crew_home):
        from kiro_crew.dashboard import server

        monkeypatch.setattr(server, "apply_config_duration", lambda: None)
        calls: list[str] = []

        class _Result:
            active = True
            ttl = 0

        monkeypatch.setattr(
            server, "grant_declared_yolo", lambda: (calls.append("granted"), _Result())[1]
        )
        return server, calls

    def test_a_stranded_config_key_grants_nothing(self, startup, caplog):
        server, calls = startup
        with caplog.at_level("WARNING"):
            server._apply_startup_yolo(object(), self._cfg(declared=True))
        assert calls == []
        assert "no longer grants" in caplog.text

    def test_the_keystone_grants_and_logs_no_migration_warning(self, startup, caplog, crew_home):
        server, calls = startup
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        _activate_grant()  # record trusted provenance for the operator's grant
        with caplog.at_level("WARNING"):
            server._apply_startup_yolo(object(), self._cfg(declared=False))
        assert calls == ["granted"]
        assert "no longer grants" not in caplog.text

    def test_neither_set_grants_nothing_and_says_nothing(self, startup, caplog):
        server, calls = startup
        with caplog.at_level("WARNING"):
            server._apply_startup_yolo(object(), self._cfg(declared=False))
        assert calls == []
        assert "no longer grants" not in caplog.text


class TestTheGrantNeedsTheMaskItRestsOn:
    """The keystone's integrity IS the bind mask, so a startup that will not mask the
    leaf must not honour the grant.

    An unconfined agent subprocess sees the real data home. No mount mask covers the
    leaf there, so that subprocess can create the directory and write the document
    itself, and the next startup reads what it wrote as the operator's standing
    authority. The document is an authorization record only while something outside
    the agent's reach holds it.

    Two resolved states are unconfined, which is why the refusal has to be keyed on
    whether the leaf is masked for agent subprocesses and NOT on the value of
    ``agent.sandbox``:

    * the mode resolves to ``off`` for the spawn, and
    * the mode is ``auto``, no backend is available, and the operator has taken the
      ``agent.sandbox_allow_unsandboxed_exec`` opt-in.

    ``test_a_floor_that_clamps_off_back_up_still_grants`` is the counterweight, and it
    is what refuses a gate spelled as ``cfg.agent.sandbox == "off"``: a governance floor
    raises a requested ``off`` to a confined tier, so that host IS masked and must keep
    granting.
    """

    def _cfg(self, *, sandbox_mode: str, declared: bool = False):
        class _Agent:
            dangerously_skip_permissions = declared
            sandbox = sandbox_mode
            sandbox_allow_unsandboxed_exec = False

        class _Cfg:
            agent = _Agent()

        return _Cfg()

    @pytest.fixture()
    def startup(self, monkeypatch, crew_home):
        from kiro_crew.dashboard import server

        monkeypatch.setattr(server, "apply_config_duration", lambda: None)
        calls: list[str] = []

        class _Result:
            active = True
            ttl = 0

        monkeypatch.setattr(
            server, "grant_declared_yolo", lambda: (calls.append("granted"), _Result())[1]
        )
        return server, calls

    def test_an_unconfined_startup_refuses_the_grant(self, startup, crew_home, caplog):
        server, calls = startup
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        with caplog.at_level("WARNING"):
            server._apply_startup_yolo(object(), self._cfg(sandbox_mode="off"))
        assert calls == []
        assert "is UNAVAILABLE on this host" in caplog.text

    def test_a_confined_startup_grants(self, startup, crew_home):
        server, calls = startup
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        _activate_grant()
        server._apply_startup_yolo(object(), self._cfg(sandbox_mode="auto"))
        assert calls == ["granted"]

    def test_a_floor_that_clamps_off_back_up_still_grants(self, startup, crew_home, monkeypatch):
        server, calls = startup
        monkeypatch.setattr(sandbox, "_governance_sandbox_floor", lambda: "cc")
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        _activate_grant(mode="off")  # the floor clamps "off" up to a masked tier
        server._apply_startup_yolo(object(), self._cfg(sandbox_mode="off"))
        assert calls == ["granted"]

    def test_no_backend_refuses_even_with_the_unsandboxed_opt_in(
        self, startup, crew_home, monkeypatch
    ):
        server, calls = startup
        monkeypatch.setattr(sandbox, "detect_backend", lambda config_mode="auto": "none")
        monkeypatch.setattr(sandbox, "_allow_unsandboxed_exec", lambda: True)
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        server._apply_startup_yolo(object(), self._cfg(sandbox_mode="auto"))
        assert calls == []

    def test_an_unmasked_host_with_no_grant_stays_silent(self, startup, crew_home, caplog):
        server, calls = startup
        with caplog.at_level("WARNING"):
            server._apply_startup_yolo(object(), self._cfg(sandbox_mode="off"))
        assert calls == []
        assert "is UNAVAILABLE on this host" not in caplog.text


class TestTheMaskIsPlatformBound:
    """Where Crew's own mask runs, and therefore where the standing grant exists.

    The mask is a Linux bind mount or a macOS Seatbelt profile, and ``wrap_argv`` hands
    the spawn to kiro-cli's internal sandbox on Windows and on macOS where that sandbox
    is enabled. A delegated spawn is confined, but by something that applies none of
    Crew's masks, so the leaf is reachable from inside it and the declaration is not an
    authorization there.

    ``test_an_undelegated_macos_host_masks`` is the control that keeps this about
    delegation rather than about macOS: the same platform masks when kiro-cli's own
    sandbox is off.
    """

    @pytest.fixture(autouse=True)
    def _the_real_predicate(self, monkeypatch, crew_home):
        """Restore the genuine delegation predicate; the module fixture pins it away."""
        monkeypatch.setattr(sandbox, "spawn_delegates_masking", _REAL_SPAWN_DELEGATES_MASKING)
        monkeypatch.setattr(sandbox, "kiro_internal_sandbox_enabled", lambda: False)
        monkeypatch.setattr(sandbox.sys, "platform", "linux")

    def test_a_maskable_linux_host_masks(self):
        assert standing_approval._keystone_is_masked("auto") is True

    def test_windows_can_never_mask(self, monkeypatch):
        monkeypatch.setattr(sandbox.sys, "platform", "win32")
        assert standing_approval._keystone_is_masked("auto") is False

    def test_delegated_macos_cannot_mask(self, monkeypatch):
        monkeypatch.setattr(sandbox.sys, "platform", "darwin")
        monkeypatch.setattr(sandbox, "kiro_internal_sandbox_enabled", lambda: True)
        assert standing_approval._keystone_is_masked("auto") is False

    def test_an_undelegated_macos_host_masks(self, monkeypatch):
        monkeypatch.setattr(sandbox.sys, "platform", "darwin")
        assert standing_approval._keystone_is_masked("auto") is True

    def test_a_delegated_host_refuses_a_written_grant_at_startup(self, monkeypatch, crew_home):
        """The platform answer reaches the startup path, not just the predicate."""
        from kiro_crew.dashboard import server

        monkeypatch.setattr(server, "apply_config_duration", lambda: None)
        monkeypatch.setattr(sandbox.sys, "platform", "win32")
        calls: list[str] = []
        monkeypatch.setattr(server, "grant_declared_yolo", lambda: calls.append("granted"))
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})

        class _Agent:
            dangerously_skip_permissions = False
            sandbox = "auto"

        class _Cfg:
            agent = _Agent()

        server._apply_startup_yolo(object(), _Cfg())
        assert calls == []


class TestAnOuterCrewSandboxIsNotTheMask:
    """A process already inside a Crew sandbox does not inherit a keystone mask.

    ``wrap_argv`` passes an in-sandbox spawn through rather than nesting, so no adapter
    mask is installed for the child. The outer sandbox still confines it, but it was
    built for its own tier's hidden dirs -- which deliberately leave ``~/.aws``,
    ``~/.ssh`` and ``~/.kube`` readable for kiro-cli's sake -- so it names no rule over
    this leaf. ``credential_mask_applies`` is where that is decided, and the grant
    inherits the refusal by composing it.
    """

    def test_an_in_sandbox_startup_cannot_mask(self, monkeypatch, crew_home):
        monkeypatch.setenv(sandbox._IN_SANDBOX_MARKER, "1")
        assert standing_approval._keystone_is_masked("auto") is False

    def test_clearing_the_marker_restores_the_mask(self, monkeypatch, crew_home):
        """Control: the same host masks once it is not inside a sandbox itself."""
        monkeypatch.delenv(sandbox._IN_SANDBOX_MARKER, raising=False)
        assert standing_approval._keystone_is_masked("auto") is True

    def test_an_in_sandbox_startup_refuses_a_written_grant(self, monkeypatch, crew_home):
        """The refusal reaches the startup path, not just the predicate."""
        from kiro_crew.dashboard import server

        monkeypatch.setattr(server, "apply_config_duration", lambda: None)
        monkeypatch.setenv(sandbox._IN_SANDBOX_MARKER, "1")
        calls: list[str] = []
        monkeypatch.setattr(server, "grant_declared_yolo", lambda: calls.append("granted"))
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})

        class _Agent:
            dangerously_skip_permissions = False
            sandbox = "auto"

        class _Cfg:
            agent = _Agent()

        server._apply_startup_yolo(object(), _Cfg())
        assert calls == []


class TestTheLeafMustSitInsideTheMaskedSet:
    """The one question the two sandbox predicates do not answer for this grant.

    A host can build Crew's mask and still name no rule over THIS leaf, so a masked
    host is a necessary condition and not a sufficient one. Containment rather than
    equality, because the masked target is the directory and the grant is a file in it.
    """

    def test_a_leaf_outside_every_masked_target_is_refused(self, monkeypatch, crew_home):
        monkeypatch.setattr(
            sandbox, "_crew_hidden_sandbox_targets", lambda: (str(crew_home / "elsewhere"),)
        )
        assert standing_approval._keystone_is_masked("auto") is False

    def test_a_leaf_inside_a_masked_target_is_covered(self, monkeypatch, crew_home):
        """Control: the same host covers the leaf once a target contains it."""
        monkeypatch.setattr(
            sandbox,
            "_crew_hidden_sandbox_targets",
            lambda: (str(loader.standing_approval_path().parent),),
        )
        assert standing_approval._keystone_is_masked("auto") is True


class TestTheNoticeMatchesTheMask:
    """A remedy is printed only where writing the document would actually grant."""

    def test_a_maskable_host_gets_the_posix_remedy(self, crew_home):
        text = standing_approval.migration_notice("auto", masked=True, windows=False)
        assert "create the file" in text
        assert "printf" not in text
        assert "UNAVAILABLE" not in text

    def test_the_masked_remedy_documents_the_two_restart_activation(self, crew_home):
        """Secondary finding: under trusted-init, the FIRST restart after the file is
        created only lays the anchor and still requires approvals; activation is
        RESTART -> REWRITE -> SECOND RESTART. The notice must say so on both platforms, or
        an operator who wrote the file sees the first restart keep prompting and concludes
        the keystone is broken.

        The REWRITE step must be a REPLACEMENT (a delete-and-recreate that yields a new
        inode), not an in-place edit: provenance binds to the file's (st_dev, st_ino,
        granted_at) identity, and an in-place edit preserves both the inode and the
        self-declared granted_at, so the document still matches the pre_anchor identity and
        the second restart refuses it. The notice must not tell the operator an in-place
        edit is enough.
        """
        for windows in (False, True):
            text = standing_approval.migration_notice("auto", masked=True, windows=windows)
            low = text.lower()
            assert "two restarts" in low
            assert "first restart" in low
            assert "second time" in low
            assert "anchor" in low
            # The remedy must require a distinct file identity, not an in-place edit.
            assert "replace" in low
            assert "delete it and create it again" in low
            assert "inode" in low
            assert "an edit in place is enough" not in low

    def test_a_maskable_host_gets_the_windows_wording(self, crew_home):
        text = standing_approval.migration_notice("auto", masked=True, windows=True)
        assert "create the file" in text
        assert "mkdir -p" not in text

    @pytest.mark.parametrize("windows", [False, True])
    def test_an_unmasked_host_is_told_the_grant_is_unavailable(self, crew_home, windows):
        text = standing_approval.migration_notice("auto", masked=False, windows=windows)
        assert "UNAVAILABLE" in text
        assert "mkdir -p" not in text
        assert "create the file" not in text
        assert "yolo_duration" in text

    def test_the_notice_resolves_the_mask_from_the_mode_it_is_given(self, crew_home, monkeypatch):
        monkeypatch.setattr(sandbox, "_governance_sandbox_floor", lambda: None)
        monkeypatch.setattr(sandbox, "detect_backend", lambda config_mode="auto": "namespace")
        monkeypatch.setattr(sandbox, "kiro_internal_sandbox_enabled", lambda: False)
        monkeypatch.setattr(platform_compat, "IS_WINDOWS", False)
        monkeypatch.setattr(platform_compat, "IS_MACOS", False)
        assert "create the file" in standing_approval.migration_notice("auto", windows=False)
        assert "UNAVAILABLE" in standing_approval.migration_notice("off", windows=False)


class TestTrustedInitProvenance:
    """Content on disk does not grant: the gateway must hold TRUSTED PROVENANCE for it.

    On prior releases ``standing-approval/`` was unmasked and agent-writable, so a hostile
    pre-upgrade agent could plant ``grant.json`` = ``True`` and wait for an upgrade to read
    it back as the operator's standing authority. The mask stops in-session writes GOING
    FORWARD but cannot tell an operator-written document from a pre-seeded one. The
    discriminator is a gateway-held provenance record on a NEW leaf that never existed on a
    prior release (so a pre-upgrade agent could not seed it), bound to the grant document's
    vetted inode identity + ``granted_at`` and established only after this version's
    first-masked-boot anchor. The invariant: no grant is honoured unless the gateway itself
    recorded provenance for it while the mask was in force and after the anchor.

    The ``maskable_host`` and ``crew_home`` fixtures put the readers on a scratch tree with
    the mask pinned in force, so every case here is about provenance and not the mask.
    """

    def _prov_path(self):
        return standing_approval._provenance_path()

    # (a) content-only grant, no provenance -> REFUSED (the pre-seed case)
    def test_a_content_only_grant_with_no_provenance_is_refused(self, crew_home):
        """A grant document present at the first masked boot has no provenance record.

        This is exactly the pre-seeded file: it is on disk before this version ever ran,
        so the gateway never recorded provenance for it. The first read lays the anchor,
        captures the document as distrusted ``pre_anchor``, and refuses.
        """
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        assert not self._prov_path().exists()  # no gateway record yet
        assert standing_approval.is_declared("auto") is False
        # The anchor was laid, and the pre-seeded document is remembered as distrusted.
        record = json.loads(self._prov_path().read_text())
        assert isinstance(record["anchor"], dict)
        assert record["grant"] is None
        assert len(record["pre_anchor"]) == 1

    def test_a_preseeded_grant_stays_refused_on_every_subsequent_boot(self, crew_home):
        """A file present at first boot never becomes trusted just by sitting there.

        The pre-seed attack is defeated only if re-reading the SAME unchanged document
        keeps refusing it -- otherwise an attacker's file is honoured on boot two.
        """
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        assert standing_approval.is_declared("auto") is False  # anchor + refuse
        # Boot after boot, the unchanged pre-seeded document stays refused.
        for _ in range(3):
            assert standing_approval.is_declared("auto") is False

    # (b) grant WITH valid matching gateway-held provenance -> honoured
    def test_a_grant_re_established_after_the_anchor_is_honoured(self, crew_home):
        """The operator's real flow: write, boot (anchor+refuse), re-write, boot (honour).

        Re-writing the keystone AFTER the anchor gives the document a new inode identity
        that does not match the distrusted ``pre_anchor`` entry, so the gateway records
        provenance for it and honours it.
        """
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        assert standing_approval.is_declared("auto") is False  # anchor laid
        # Operator re-establishes with a distinct granted_at, so the identity leaves the
        # distrusted pre_anchor set regardless of any filesystem inode recycling.
        _rewrite_grant({standing_approval.GRANT_FIELD: True, "granted_at": "2026-09-28T00:00:00Z"})
        assert standing_approval.is_declared("auto") is True  # provenance recorded + granted
        record = json.loads(self._prov_path().read_text())
        assert isinstance(record["grant"], dict)
        # And it stays honoured on the next boot from the recorded provenance.
        assert standing_approval.is_declared("auto") is True

    def test_the_recorded_provenance_binds_the_vetted_inode_identity(self, crew_home):
        """Provenance names the exact (st_dev, st_ino) the document was read from."""
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        _activate_grant()
        assert standing_approval.is_declared("auto") is True
        record = json.loads(self._prov_path().read_text())
        st = os.stat(loader.standing_approval_path())
        assert record["grant"]["st_dev"] == st.st_dev
        assert record["grant"]["st_ino"] == st.st_ino

    # (c) provenance mismatch -> refused
    def test_a_grant_matching_a_pre_anchor_identity_is_refused(self, crew_home):
        """The refusal path: a document whose identity matches a distrusted ``pre_anchor``
        entry is the pre-seeded file and is refused, even though the anchor now exists.
        Only re-establishing it (a new inode / ``granted_at``, so it leaves ``pre_anchor``)
        makes the gateway record and honour it.
        """
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        assert standing_approval.is_declared("auto") is False  # anchor + capture pre_anchor
        record = json.loads(self._prov_path().read_text())
        st = os.stat(loader.standing_approval_path())
        # The on-disk document's identity IS the captured pre_anchor entry.
        assert record["pre_anchor"][0]["st_dev"] == st.st_dev
        assert record["pre_anchor"][0]["st_ino"] == st.st_ino
        # So every subsequent boot of the unchanged document is refused.
        assert standing_approval.is_declared("auto") is False
        # Re-establishing (new inode AND a fresh granted_at, so the identity leaves
        # pre_anchor regardless of whether the filesystem recycled the inode) is honoured.
        _rewrite_grant({standing_approval.GRANT_FIELD: True, "granted_at": "2026-09-28T00:00:00Z"})
        assert standing_approval.is_declared("auto") is True

    def test_a_grant_whose_recorded_provenance_no_longer_matches_is_not_honoured_blindly(
        self, crew_home
    ):
        """Provenance binds ``granted_at`` too: a recorded ``grant`` whose ``granted_at``
        differs from the on-disk document does not match, so the stale record cannot honour
        a changed document. When the changed document is also a distrusted ``pre_anchor``
        identity, it is refused.
        """
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True, "granted_at": "2026-01-01"})
        _activate_grant({standing_approval.GRANT_FIELD: True, "granted_at": "2026-01-01"})
        assert standing_approval.is_declared("auto") is True
        recorded = json.loads(self._prov_path().read_text())["grant"]
        assert recorded["granted_at"] == "2026-01-01"

        # Pin the recorded provenance to a DIFFERENT granted_at, and mark the on-disk
        # document's identity as pre-anchor so the mismatch path is what decides. The
        # record is RE-SIGNED under the gateway key, so it still verifies -- otherwise the
        # reader would treat it as a forged record and re-lay the anchor instead of taking
        # the mismatch branch this test is about.
        prov = json.loads(self._prov_path().read_text())
        prov["grant"]["granted_at"] = "1999-12-31"
        st = os.stat(loader.standing_approval_path())
        prov["pre_anchor"] = [
            {"st_dev": st.st_dev, "st_ino": st.st_ino, "granted_at": "2026-01-01"}
        ]
        _write_signed_provenance(prov)
        # The document matches a pre_anchor identity (distrusted) and does NOT match the
        # recorded grant (granted_at differs), so it is refused.
        assert standing_approval.is_declared("auto") is False

    # (d) anchor / first-boot behaviour
    def test_the_first_masked_boot_lays_an_anchor_and_grants_nothing(self, crew_home):
        """With no grant present, the first boot still refuses and, because there is no
        grant to honour, does not even need the anchor yet -- an absent grant short-circuits
        before provenance. The anchor is laid the first time a DECLARED grant is seen.
        """
        assert not self._prov_path().exists()
        assert standing_approval.is_declared("auto") is False
        # No declared grant -> no anchor written (nothing to distrust).
        assert not self._prov_path().exists()

    def test_a_corrupt_provenance_leaf_is_treated_as_no_anchor(self, crew_home):
        """An unparseable provenance record fails closed to 'no anchor', so a declared
        grant is refused and the anchor is re-laid rather than a corrupt record honoured.
        """
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        self._prov_path().parent.mkdir(parents=True, exist_ok=True)
        self._prov_path().write_text("{not json")
        assert standing_approval.is_declared("auto") is False
        # Re-laid as a fresh valid anchor.
        record = json.loads(self._prov_path().read_text())
        assert isinstance(record["anchor"], dict)

    def test_the_provenance_leaf_lives_in_the_masked_keystone_directory(self):
        """The record inherits the mask because it sits INSIDE ``standing-approval/`` --
        the same directory the grant lives in, already bind-masked and precreated -- so it
        needs no new leaf registration and could not have been seeded on a prior release.
        """
        assert self._prov_path().parent.name == loader.STANDING_APPROVAL_DIRNAME
        assert self._prov_path().name == standing_approval._PROVENANCE_FILENAME
        assert self._prov_path() != loader.standing_approval_path()

    def test_the_provenance_reader_refuses_a_symlinked_leaf(self, crew_home):
        """The provenance reader keeps the grant reader's nofollow discipline: a symlink
        at the record's name is refused, so a declared grant with only a symlinked
        provenance record is not honoured (the anchor is re-laid instead).
        """
        if sys.platform == "win32":
            pytest.skip("POSIX symlink semantics")
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        elsewhere = crew_home.parent / "aliased-provenance.json"
        elsewhere.write_text(
            json.dumps(
                {"anchor": {"established_at": "2026-01-01"}, "grant": None, "pre_anchor": []}
            )
        )
        self._prov_path().parent.mkdir(parents=True, exist_ok=True)
        self._prov_path().symlink_to(elsewhere)
        # The symlinked record is refused -> read as no anchor -> declared grant refused.
        assert standing_approval.is_declared("auto") is False

    def test_the_provenance_writer_swaps_a_planted_symlink_out(self, crew_home):
        """The writer uses atomic replace, so a symlink pre-planted at the record's name
        is swapped OUT rather than followed -- the anchor lands on a real regular file.
        """
        if sys.platform == "win32":
            pytest.skip("POSIX symlink semantics")
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        target = crew_home.parent / "writer-target.json"
        target.write_text("untouched")
        self._prov_path().parent.mkdir(parents=True, exist_ok=True)
        self._prov_path().symlink_to(target)

        standing_approval.is_declared("auto")  # first boot writes the anchor

        assert not self._prov_path().is_symlink()  # the symlink was swapped out
        assert self._prov_path().is_file()
        assert target.read_text() == "untouched"  # the planted target was not followed


class TestTheAnchorIsAuthenticated:
    """The anchor is not merely a NEW leaf -- it is SIGNED under a gateway-only key.

    GPT 5.6's blocking finding: on a prior release the whole ``standing-approval/``
    directory was agent-writable and absent, so a pre-upgrade agent could create the
    directory and plant BOTH ``grant.json`` AND a forged ``provenance.json`` (an empty
    ``anchor`` dict + empty ``pre_anchor`` + no ``grant``) whose STRUCTURE defeats a
    structure-only check and yields silent permanent auto-approval. The v1 code trusted any
    on-disk ``anchor`` object. The fix authenticates the record with an HMAC under a key the
    gateway generates fresh on the first masked boot -- a key a pre-upgrade agent provably
    could not have (it never existed on a prior release, and a planted key file is
    overwritten at anchor time), and cannot read afterwards (this version masks the whole
    directory). Verification fails closed on an absent/short key or a bad signature.

    The ``maskable_host`` + ``crew_home`` fixtures pin the mask in force on a scratch tree,
    so every case here is about the signature and nothing else.
    """

    def _prov_path(self):
        return standing_approval._provenance_path()

    def _key_path(self):
        return standing_approval._provenance_key_path()

    # (a) A fully pre-seeded provenance.json + grant.json is REFUSED (bad signature).
    def test_a_forged_provenance_record_with_no_signature_is_refused(self, crew_home):
        """The exact GPT defeat: plant grant.json=True AND a forged provenance.json with an
        empty anchor dict + empty pre_anchor + no grant. The v1 code honoured this. Now the
        forged record carries no valid ``sig``, so it does not verify -> treated as no
        anchor -> the anchor is re-laid (SIGNED) and the planted grant is captured as
        distrusted pre_anchor -> refused. The attacker never had the gateway key.
        """
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        # Attacker plants the directory + a forged provenance record with the winning shape.
        self._prov_path().parent.mkdir(parents=True, exist_ok=True)
        self._prov_path().write_text(json.dumps({"anchor": {}, "pre_anchor": [], "grant": None}))
        assert standing_approval.is_declared("auto") is False
        # The forged record was replaced by a freshly SIGNED anchor, and the planted grant
        # is remembered as distrusted -- so it stays refused on every subsequent boot.
        record = json.loads(self._prov_path().read_text())
        assert isinstance(record.get("sig"), str) and record["sig"]
        assert record["grant"] is None
        assert len(record["pre_anchor"]) == 1
        assert standing_approval.is_declared("auto") is False

    def test_a_forged_record_carrying_a_wrong_signature_is_refused(self, crew_home):
        """A pre-upgrade agent that GUESSES a signature string still fails: the HMAC is over
        the record fields under a key it does not hold, so a fabricated ``sig`` does not
        verify and the record is treated as no anchor.
        """
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        self._prov_path().parent.mkdir(parents=True, exist_ok=True)
        st = os.stat(loader.standing_approval_path())
        self._prov_path().write_text(
            json.dumps(
                {
                    "anchor": {"established_at": "2026-01-01"},
                    "pre_anchor": [],
                    "grant": {"st_dev": st.st_dev, "st_ino": st.st_ino, "granted_at": None},
                    "sig": "deadbeef" * 8,
                }
            )
        )
        assert standing_approval.is_declared("auto") is False

    # (b) A validly-signed post-anchor grant is honoured.
    def test_a_gateway_signed_post_anchor_grant_is_honoured(self, crew_home):
        """The operator's real flow yields a SIGNED record that verifies and is honoured,
        and it stays honoured on the next boot from the recorded (signed) provenance.
        """
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        _activate_grant()  # anchor (signed) -> re-write -> record grant (signed) -> honour
        assert standing_approval.is_declared("auto") is True
        record = json.loads(self._prov_path().read_text())
        assert isinstance(record.get("sig"), str) and record["sig"]
        assert standing_approval._verify_provenance(record) is True
        assert standing_approval.is_declared("auto") is True  # honoured again next boot

    # (c) A tampered signature (record edited after signing) is refused.
    def test_tampering_the_signed_record_after_signing_is_refused(self, crew_home):
        """Editing a field of a validly-signed record without re-signing breaks the HMAC,
        so the record no longer verifies -> treated as no anchor -> the honoured grant stops
        being honoured (the anchor is re-laid and the now-unchanged grant is distrusted).
        """
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        _activate_grant()
        assert standing_approval.is_declared("auto") is True
        # Tamper: flip a byte of the recorded grant's st_ino WITHOUT re-signing.
        prov = json.loads(self._prov_path().read_text())
        prov["grant"]["st_ino"] = int(prov["grant"]["st_ino"]) + 1
        self._prov_path().write_text(json.dumps(prov))  # NOT re-signed
        assert standing_approval._verify_provenance(prov) is False
        assert standing_approval.is_declared("auto") is False

    # (d) Verification fails closed when the signing key is absent.
    def test_verification_fails_closed_when_the_key_is_absent(self, crew_home):
        """A validly-signed, honoured record must stop being honoured if the gateway key
        disappears: with no key, nothing can be verified, so the record is not trusted.
        This is the fail-closed property -- an unauthenticatable record grants nothing.
        """
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        _activate_grant()
        assert standing_approval.is_declared("auto") is True
        assert self._key_path().exists()
        # Remove the key the record was signed under.
        self._key_path().unlink()
        assert standing_approval._read_provenance_key_bytes() is None
        record = json.loads(self._prov_path().read_text())
        assert standing_approval._verify_provenance(record) is False
        # With no key the record cannot verify, so it is treated as no anchor and refused.
        # (A fresh key is then generated, but the once-honoured grant is now distrusted.)
        assert standing_approval.is_declared("auto") is False

    def test_a_short_key_fails_closed(self, crew_home):
        """A key file shorter than the required length is not a usable key: verification
        fails closed rather than accepting a weak or truncated key.
        """
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        _activate_grant()
        assert standing_approval.is_declared("auto") is True
        record = json.loads(self._prov_path().read_text())
        # Overwrite the key with too few bytes.
        self._key_path().write_bytes(b"x" * (standing_approval._PROVENANCE_KEY_BYTES - 1))
        assert standing_approval._read_provenance_key_bytes() is None
        assert standing_approval._verify_provenance(record) is False
        assert standing_approval.is_declared("auto") is False

    # The key itself is unforgeable: a planted key is overwritten at anchor time.
    def test_a_pre_seeded_key_is_overwritten_at_anchor_time(self, crew_home):
        """A pre-upgrade agent could plant a ``provenance.key`` while the dir was writable.
        The gateway must NOT adopt it: on the anchor-writing boot it generates fresh entropy
        and swaps the planted key OUT, so the record is signed under a key the attacker never
        knew. The planted bytes are not the key the record is trusted under.
        """
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        planted = b"A" * standing_approval._PROVENANCE_KEY_BYTES
        self._key_path().parent.mkdir(parents=True, exist_ok=True)
        self._key_path().write_bytes(planted)

        standing_approval.is_declared("auto")  # first masked boot: regenerates the key

        assert self._key_path().read_bytes() != planted  # planted key was overwritten
        assert len(self._key_path().read_bytes()) >= standing_approval._PROVENANCE_KEY_BYTES
        # A forged record the attacker signed with the PLANTED key does not verify under the
        # gateway's fresh key, so it is refused.
        st = os.stat(loader.standing_approval_path())
        forged = {
            "anchor": {"established_at": "2026-01-01"},
            "pre_anchor": [],
            "grant": {"st_dev": st.st_dev, "st_ino": st.st_ino, "granted_at": None},
        }
        forged["sig"] = standing_approval._sign_provenance(forged, planted)
        self._prov_path().write_text(json.dumps(forged, sort_keys=True))
        assert standing_approval._verify_provenance(forged) is False
        assert standing_approval.is_declared("auto") is False

    def test_the_key_leaf_lives_in_the_masked_keystone_directory(self):
        """The key inherits the mask because it sits INSIDE ``standing-approval/`` -- the
        same already-masked, precreated directory -- so it needs no new leaf registration
        and could not have been read on a prior release once this version masks it.
        """
        assert self._key_path().parent.name == loader.STANDING_APPROVAL_DIRNAME
        assert self._key_path().name == standing_approval._PROVENANCE_KEY_FILENAME
        assert self._key_path() != self._prov_path()
        assert self._key_path() != loader.standing_approval_path()

    def test_the_key_is_written_owner_only(self, crew_home):
        """The freshly generated key is 0o600, like every other keystone secret."""
        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        standing_approval.is_declared("auto")  # generate the key
        if os.name == "posix":
            assert (self._key_path().stat().st_mode & 0o077) == 0

    def test_the_key_reader_refuses_a_symlinked_key(self, crew_home):
        """The key reader keeps the nofollow discipline: a symlinked key is refused, so a
        record cannot be authenticated against a key reached through an operator-planted
        alias.
        """
        if sys.platform == "win32":
            pytest.skip("POSIX symlink semantics")
        elsewhere = crew_home.parent / "aliased-key"
        elsewhere.write_bytes(b"z" * standing_approval._PROVENANCE_KEY_BYTES)
        self._key_path().parent.mkdir(parents=True, exist_ok=True)
        self._key_path().symlink_to(elsewhere)
        assert standing_approval._read_provenance_key_bytes() is None


class TestRevalidateStandingOverrideOnLiveSandboxChange:
    """F1: a LIVE ``agent.sandbox`` flip must revalidate (and revoke) the standing grant.

    ``agent.sandbox`` is not restart-gated -- ``config.sections`` marks it without
    ``restart`` and its own doc says a change "applies to sessions started after it" -- so
    a flip from a masked mode to an unmasked one (e.g. ``off``) at runtime removes the mask
    precondition the declared standing grant rests on. The declared grant is evaluated once
    at boot and held permanently in memory, so without revalidation a new UNMASKED session
    would inherit permanent auto-approval AND be able to reach the keystone itself.

    ``safety_override.revalidate_standing_override`` is the trusted-applier hook (wired onto
    the always-constructed GatewayOrchestrator's live-config watcher). These tests exercise
    it directly against a real provenanced grant established through the same harness the
    rest of this module uses.
    """

    @pytest.fixture(autouse=True)
    def _fresh_override(self):
        from kiro_crew import safety_override

        safety_override.reset_singleton()
        yield
        safety_override.reset_singleton()

    def _arm_declared_grant(self, crew_home, mode: str = "auto"):
        """Establish a provenanced masked grant AND install the declared override in memory,
        exactly as ``grant_declared_yolo`` does at boot."""
        from kiro_crew import safety_override

        _write_grant(crew_home, {standing_approval.GRANT_FIELD: True})
        _activate_grant(mode=mode)
        # Sanity: the on-disk declaration is honoured under the masked mode.
        assert standing_approval.is_declared(mode) is True
        result = safety_override.grant_declared_yolo()
        assert result.active is True
        so = safety_override.safety_override()
        assert so.is_declared is True  # the LIVE grant is the operator's declared grant
        return so

    def test_revokes_the_declared_override_when_the_flip_is_to_an_unmasked_mode(self, crew_home):
        from kiro_crew import safety_override

        so = self._arm_declared_grant(crew_home, mode="auto")
        # 'off' does not mask the keystone, so the declaration is no longer an authorization.
        assert standing_approval.is_declared("off") is False

        revoked = safety_override.revalidate_standing_override("off")

        assert revoked is True
        # The override is dropped from in-memory state BEFORE any 'off' session runs.
        assert so.is_declared is False
        assert so.is_active() is False

    def test_retains_the_declared_override_when_the_new_mode_still_masks(self, crew_home):
        from kiro_crew import safety_override

        so = self._arm_declared_grant(crew_home, mode="auto")
        # 'strict' still masks the keystone (a masking mode), so the grant is legitimate.
        assert standing_approval.is_declared("strict") is True

        revoked = safety_override.revalidate_standing_override("strict")

        assert revoked is False
        assert so.is_declared is True
        assert so.is_active() is True

    def test_revocation_precedes_any_session_governed_by_the_new_mode(self, crew_home):
        """The revocation is synchronous in the applier, so by the time it returns the
        in-memory override is already gone -- a session started under the new mode cannot
        observe the stale grant."""
        from kiro_crew import safety_override

        so = self._arm_declared_grant(crew_home, mode="auto")
        assert so.is_active() is True

        safety_override.revalidate_standing_override("off")

        # No window: the state is dropped the moment the trusted applier returns.
        assert so.is_active() is False
        assert so.is_declared is False

    def test_does_not_touch_an_adhoc_grant(self, crew_home):
        """Only the DECLARED grant is mask-derived. An operator's ad-hoc timed grant is
        their own decision and must survive a sandbox flip untouched."""
        from kiro_crew import safety_override

        so = safety_override.safety_override()
        so.activate(source="dashboard", ttl=3600)
        assert so.is_active() is True
        assert so.is_declared is False

        revoked = safety_override.revalidate_standing_override("off")

        assert revoked is False
        assert so.is_active() is True  # the ad-hoc grant is untouched

    def test_noop_when_no_grant_is_active(self, crew_home):
        from kiro_crew import safety_override

        so = safety_override.safety_override()
        assert so.is_active() is False

        assert safety_override.revalidate_standing_override("off") is False
        assert so.is_active() is False

    # ── DERIVED trust is torn down synchronously on the flip (GPT F1) ──
    #
    # ``deactivate`` drops only in-memory grant state. A declared grant is
    # session-wide, so it also wrote ``approval_policy="auto"`` onto its slots and
    # into the shared channel-trust mapping, and ``subagent_manager.admission.
    # parent_trusted`` reads THAT policy directly (gate.py:1270-1272,1288-1290). The
    # revalidation must invoke the SAME synchronous ``on_policy_revoked`` teardown the
    # operator-revoke path uses, BEFORE the deactivate, so a spawn admitted right after
    # the flip cannot read a stale "auto". These mirror the ``on_policy_revoked``-clears-
    # a-``policies``-dict harness in ``test_approval_modes_enforcement`` and the
    # gate's own ``approval_policy == "auto"`` read.

    def _derived_trust_model(self):
        """The two stores the gateway's ``_clear_override_derived_trust`` clears: a
        per-slot ``approval_policy`` map (what ``admission.parent_trusted`` reads) and
        the shared channel-trust mapping. Returns them plus a ``_clear`` callback with
        the same effect the real hook has, and a list recording revalidation order."""
        policies = {"dashboard:s1": "auto", "channel:telegram:owner": "auto"}
        channel_mapping = {"channel:telegram:owner"}
        order: list[str] = []

        def _clear(_source):
            order.append("clear")
            for key in list(policies):
                policies[key] = ""
            channel_mapping.clear()

        return policies, channel_mapping, order, _clear

    def test_the_flip_clears_slot_and_channel_derived_trust_synchronously(self, crew_home):
        from kiro_crew import safety_override

        so = self._arm_declared_grant(crew_home, mode="auto")
        policies, channel_mapping, _order, _clear = self._derived_trust_model()
        so.on_policy_revoked = _clear

        revoked = safety_override.revalidate_standing_override("off")

        assert revoked is True
        # By the time the trusted applier returns, a spawn admission read of the
        # parent slot's policy sees no "auto" -- the exact value gate.py consults.
        assert policies["dashboard:s1"] == ""
        # The shared channel-trust mapping (the CHANNEL half a subagent reads) is gone.
        assert policies["channel:telegram:owner"] == ""
        assert channel_mapping == set()
        assert so.is_active() is False

    def test_teardown_runs_before_the_grant_is_deactivated(self, crew_home):
        """Ordering MUST be clear-derived-trust -> deactivate (safety_override.py
        docs the grant-first ordering as the unrecoverable one: ``is_active()`` would
        report no grant while the slots still carry "auto")."""
        from kiro_crew import safety_override

        so = self._arm_declared_grant(crew_home, mode="auto")
        _policies, _channel_mapping, order, _clear = self._derived_trust_model()

        # Record when the grant flag actually drops, relative to the teardown.
        real_deactivate = so.deactivate

        def _recording_deactivate(*a, **kw):
            order.append("deactivate")
            return real_deactivate(*a, **kw)

        so.deactivate = _recording_deactivate  # type: ignore[method-assign]
        so.on_policy_revoked = _clear

        safety_override.revalidate_standing_override("off")

        assert order == ["clear", "deactivate"], (
            "derived-trust teardown must run BEFORE deactivate, or a spawn in the gap "
            f"is auto-approved against the unmasked sandbox; saw {order}"
        )

    def test_a_retained_grant_does_not_tear_down_derived_trust(self, crew_home):
        """When the new mode still masks (grant retained), the teardown must NOT run --
        the operator's inherited trust is still legitimate."""
        from kiro_crew import safety_override

        so = self._arm_declared_grant(crew_home, mode="auto")
        policies, channel_mapping, order, _clear = self._derived_trust_model()
        so.on_policy_revoked = _clear

        revoked = safety_override.revalidate_standing_override("strict")

        assert revoked is False
        assert order == []
        assert policies["dashboard:s1"] == "auto"
        assert channel_mapping == {"channel:telegram:owner"}

    def test_a_raising_teardown_fails_closed_and_re_raises(self, crew_home):
        """The trusted ConfigWatch applier retries on failure, so a teardown that
        raises must propagate (not be swallowed), like the rest of this fn."""
        from kiro_crew import safety_override

        so = self._arm_declared_grant(crew_home, mode="auto")

        def _boom(_source):
            raise RuntimeError("derived-trust store unreachable")

        so.on_policy_revoked = _boom

        with pytest.raises(RuntimeError, match="derived-trust store unreachable"):
            safety_override.revalidate_standing_override("off")
