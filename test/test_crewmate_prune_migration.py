"""Startup crewmate prune: only the retired sync's rows are eligible, and
member_id, a chat, the overlay or a team keeps an eligible row.

Seeds ``config.json`` rows of every shape an install can carry. A row is
removal-eligible only when its ``source`` is ``builtin``, ``package`` or
``aim``, or ``kirocrew`` bound to one of the three core runtime specs; any other
source -- an app's own stamp, any other string, a non-string -- and a
``kirocrew`` row bound to anything else are kept. An eligible row survives when
it has a ``member_id``, its Crewmates-page thread holds a turn,
``config.local.json`` names it, or a team lists it; otherwise it is removed
whether it is bound to a runtime helper spec, a private copy, an uninstalled
spec or a skill-view alias, and whatever other fields were edited.

A turn in the live transcript or an archived segment keeps a row; another
session that ran the agent and a thread opened but never written to do not.
Unreadable bindings, transcript evidence and an unreadable team document fail
closed, while links and FIFOs are no record. Delete-time checks re-run the keep
conditions under their locks: a ``member_id`` stamped, a restamp to an app's
source, a rebinding onto a kept ``kirocrew`` row, an overlay leaf or a team
placement between the scan and the delete refuses it. The request barrier,
abandon signal, marker and cross-process lock tests pin the serialization that
makes candidate checks safe.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from kiro_crew import crewmate_prune_migration as mig
from kiro_crew.config.loader import KiroCrewAgentConfig, KiroCrewConfig, config_path
from kiro_crew.memory_stores import provision_member_memory


@pytest.fixture(autouse=True)
def _agents_dir(tmp_path, monkeypatch):
    """Point the agents directory at a scratch dir through the documented hook.

    The pass never reads a spec, so nothing is written here; the tests that
    say so assert the directory stays empty.
    """
    root = tmp_path / "agents"
    root.mkdir()
    monkeypatch.setattr("kiro_crew.agent.KIRO_AGENTS_DIR", root)
    return root


def _synced(name: str, source: str = "builtin") -> KiroCrewAgentConfig:
    # Exactly what an older sync wrote: the spec's description and source.
    return KiroCrewAgentConfig(kiro_agent=name, description=f"{name} agent", source=source)


def _owner(name: str, kiro_agent: str | None = None, **kw) -> KiroCrewAgentConfig:
    # What the dashboard wrote before member_id existed: the kirocrew stamp.
    return KiroCrewAgentConfig(kiro_agent=kiro_agent or name, source="kirocrew", **kw)


_META = json.dumps({"_type": "metadata", "title": "t", "mode": "member"}) + "\n"
_TURN = json.dumps({"role": "user", "content": "hi"}) + "\n"


class _Log:
    """A conversation log: ``_dir`` holds one ``<key>.jsonl`` per session, the
    first line the metadata record the real log writes."""

    def __init__(self, root: Path):
        self._dir = root
        root.mkdir(parents=True, exist_ok=True)

    def session(self, key: str, agent: str | None = None):
        """A session elsewhere -- a plain chat, a spawn, a cron run -- that ran ``agent``."""
        meta: dict = {"_type": "metadata", "title": key}
        if agent:
            meta["agent"] = agent
        path = self._dir / f"{key}.jsonl"
        path.write_text(json.dumps(meta) + "\n" + _TURN)
        return path

    def dm(
        self,
        slug: str,
        *,
        turns: bool = True,
        raw: bytes | None = None,
        archived: str = "",
        slot_key: str = "",
    ):
        """The Crewmates-page thread of ``slug``: metadata plus a turn by default."""
        stem = f"dashboard_{slot_key or 'member-' + slug}"
        if archived:
            path = self._dir / "archive" / f"{stem}__{archived}.jsonl"
            path.parent.mkdir(exist_ok=True)
        else:
            path = self._dir / f"{stem}.jsonl"
        if raw is not None:
            path.write_bytes(raw)
        else:
            path.write_text(_META + (_TURN if turns else ""))
        return path


@pytest.fixture
def log(tmp_path):
    return _Log(tmp_path / "sessions")


@pytest.fixture
def bindings_dir(tmp_path, monkeypatch):
    """Point the DM-binding path at a scratch dir; ``write(name)`` opens a thread."""
    root = tmp_path / "dm"
    root.mkdir()
    monkeypatch.setattr("kiro_crew.members.dm_binding_path", lambda slug: root / f"{slug}.json")
    monkeypatch.setattr("kiro_crew.members.member_slug", lambda name, cfg=None: name)

    def write(
        name: str,
        *,
        member: str | None = None,
        raw: bytes | None = None,
        slot_key: str = "",
    ):
        path = root / f"{name}.json"
        if raw is not None:
            path.write_bytes(raw)
        else:
            path.write_text(
                json.dumps({"slot_key": slot_key or f"member-{name}", "member": member or name})
            )
        return path

    return write


@pytest.fixture
def old_style_config():
    """Three rows: two the sync left (``radar``, ``scout``), one made by hand."""
    cfg = KiroCrewConfig.load()
    cfg.agents["radar"] = _synced("radar")
    cfg.agents["scout"] = _synced("scout")
    cfg.agents["by-hand"] = KiroCrewAgentConfig(kiro_agent="radar", description="mine")
    provision_member_memory(cfg, "by-hand")
    cfg.save()
    hand = KiroCrewConfig.load().agents["by-hand"]
    assert hand.member_id and hand.memory_store != "default"


def _run(log, **kw):
    return mig.prune_synced_crewmates(log, **kw)


def _seed(**rows: KiroCrewAgentConfig) -> None:
    cfg = KiroCrewConfig.load()
    for name, row in rows.items():
        cfg.agents[name] = row
    cfg.save()


class TestThePass:
    def test_removes_never_chatted_keeps_chatted_leaves_hand_made(
        self, old_style_config, bindings_dir, log, _agents_dir
    ):
        before = KiroCrewConfig.load()
        hand_before = before.agents["by-hand"]
        assert not mig.marker_path().exists()
        bindings_dir("radar")
        log.dm("radar")  # the owner chatted with radar on the Crewmates page
        log.session("chat-1", "kirocrew")

        report = _run(log)

        assert report.removed == ["scout"]
        assert report.kept == ["radar"]
        after = KiroCrewConfig.load()
        assert "scout" not in after.agents
        # The chatted row keeps its exact binding: a memory binding is identity,
        # chosen at creation, and no startup pass rewrites it.
        assert after.agents["radar"] == before.agents["radar"]
        assert after.agents["radar"].member_id == ""
        assert after.agents["radar"].memory_store == "default"
        assert after.agents["by-hand"] == hand_before
        # The agents directory is never read or written by the pass.
        assert list(_agents_dir.iterdir()) == []
        marker = json.loads(mig.marker_path().read_text())
        assert marker["removed"] == ["scout"]
        assert marker["kept"] == ["radar"]
        assert marker["doubted"] == {}

    def test_a_session_that_ran_the_agent_elsewhere_does_not_count(
        self, old_style_config, bindings_dir, log
    ):
        # A plain chat, a spawn, a cron run or an app's own slot used the AGENT,
        # which stays installed; only the crewmate's own thread keeps the row.
        log.session("chat-2", "scout")
        log.session("dashboard_chat-3", "radar")
        report = _run(log)
        assert set(report.removed) == {"radar", "scout"}

    def test_a_thread_opened_but_never_written_to_does_not_count(
        self, old_style_config, bindings_dir, log
    ):
        # Clicking the crewmate in the roster writes the binding and at most a
        # metadata line; nobody chatted.
        bindings_dir("radar")
        log.dm("radar", turns=False)
        bindings_dir("scout")
        report = _run(log)
        assert set(report.removed) == {"radar", "scout"}

    def test_an_archived_segment_of_the_thread_counts(self, old_style_config, bindings_dir, log):
        # Compaction moved the turns into an archive segment; the live file
        # holds only the metadata line.
        log.dm("scout", turns=False)
        log.dm("scout", archived="20260901-195926")
        report = _run(log)
        assert report.kept == ["scout"]
        assert report.removed == ["radar"]

    def test_a_missing_live_transcript_without_an_archive_is_no_turn(
        self, old_style_config, bindings_dir, log
    ):
        live = log._dir / "dashboard_member-scout.jsonl"
        assert not live.exists()
        assert mig._transcript_has_a_turn(live) is False

        report = _run(log)

        assert "scout" in report.removed

    def test_another_crewmates_archive_is_not_this_ones(self, old_style_config, bindings_dir, log):
        # ``member-scout`` is a prefix of ``member-scout-2``; the segment
        # delimiter keeps them apart.
        log.dm("scout-2", archived="20260901-195926")
        report = _run(log)
        assert "scout" in report.removed

    def test_the_bindings_recorded_slot_key_is_read_too(self, old_style_config, bindings_dir, log):
        bindings_dir("scout", slot_key="member-scout-v2abc")
        log.dm("scout", slot_key="member-scout-v2abc")
        report = _run(log)
        assert report.kept == ["scout"]

    def test_a_binding_for_another_name_does_not_lend_its_thread(
        self, old_style_config, bindings_dir, log
    ):
        # A colliding slug's binding names the other crew: its thread is not
        # scout's, so its turns do not keep scout.
        bindings_dir("scout", member="someone-else", slot_key="member-elsewhere")
        log.dm("x", slot_key="member-elsewhere")
        report = _run(log)
        assert "scout" in report.removed

    def test_an_older_builds_first_line_that_is_a_message_counts(
        self, old_style_config, bindings_dir, log
    ):
        log.dm("scout", raw=_TURN.encode())
        report = _run(log)
        assert report.kept == ["scout"]

    def test_rows_with_a_sync_source_are_removed(self, bindings_dir, log):
        _seed(
            builtin=_synced("builtin", source="builtin"),
            omni=_synced("omni", source="package"),
            legacy=_synced("legacy", source="aim"),
        )
        report = _run(log)
        assert set(report.removed) == {"builtin", "omni", "legacy"}
        assert report.refused == [] and report.doubted == {}
        assert not set(report.removed) & KiroCrewConfig.load().agents.keys()

    def test_rows_with_any_other_source_are_kept(self, bindings_dir, log):
        # Apps create crewmates themselves and stamp them with their own
        # source; the roster hides those, the prune does not delete them. Any
        # other string, a near-miss spelling of a sync stamp and a non-string
        # are not the sync's row either.
        from kiro_crew.config.loader import update_config_locked

        _seed(
            improved=_synced("improved", source="auto-improvement"),
            odd=_synced("odd", source="somewhere"),
            upper=_synced("upper", source="Builtin"),
            blank=_synced("blank", source=""),
            broken=_synced("broken"),
            numeric=_synced("numeric"),
        )

        def _break_source(doc):
            doc["agents"]["broken"]["source"] = []
            doc["agents"]["numeric"]["source"] = 5
            return doc

        update_config_locked(mutate=_break_source)
        report = _run(log)
        assert report.removed == [] and report.refused == [] and report.doubted == {}
        assert {"improved", "odd", "upper", "blank", "broken", "numeric"} <= (
            KiroCrewConfig.load().agents.keys()
        )
        assert mig.marker_path().exists()

    def test_edited_rows_are_removed(self, bindings_dir, log):
        # A model, effort, avatar, colour, triggers, star, workspace, display
        # name or store is not a claim on the crewmate; only member_id, the
        # overlay, a team and a chat are.
        from kiro_crew.config.loader import update_config_locked

        _seed(
            described=KiroCrewAgentConfig(
                kiro_agent="described", description="owner wording", source="package"
            ),
            tuned=KiroCrewAgentConfig(kiro_agent="tuned", source="builtin", model="m"),
            effort=KiroCrewAgentConfig(
                kiro_agent="effort", source="builtin", reasoning_effort="high"
            ),
            routed=KiroCrewAgentConfig(kiro_agent="routed", source="builtin", triggers="x"),
            starred=KiroCrewAgentConfig(kiro_agent="starred", source="builtin", starred=True),
            labelled=KiroCrewAgentConfig(
                kiro_agent="labelled", source="builtin", display_name="Label"
            ),
            placed=KiroCrewAgentConfig(kiro_agent="placed", source="builtin", workspace="other"),
            stored=KiroCrewAgentConfig(
                kiro_agent="stored", source="builtin", memory_store="member-stored"
            ),
            pictured=_synced("pictured"),
        )

        def _decorate(doc):
            doc["agents"]["pictured"]["avatar"] = {"kind": "image"}
            doc["agents"]["pictured"]["session_color"] = "#123456"
            return doc

        update_config_locked(mutate=_decorate)
        report = _run(log)
        assert set(report.removed) == {
            "described",
            "tuned",
            "effort",
            "routed",
            "starred",
            "labelled",
            "placed",
            "stored",
            "pictured",
        }
        assert report.refused == [] and report.doubted == {}

    def test_a_row_whose_spec_is_not_installed_is_removed(self, bindings_dir, log, _agents_dir):
        _seed(gone=_synced("gone", source="package"))
        assert list(_agents_dir.iterdir()) == []
        report = _run(log)
        assert report.removed == ["gone"]
        assert "gone" not in KiroCrewConfig.load().agents

    def test_a_row_bound_to_a_private_copy_is_removed(self, bindings_dir, log):
        from kiro_crew import agent_state

        _seed(copy=_synced("copy"))
        agent_state.set_fork_info("copy", forked_from="shared", private_to="owner")
        report = _run(log)
        assert report.removed == ["copy"]

    def test_rows_bound_to_the_runtimes_own_helper_specs_are_removed(self, bindings_dir, log):
        # The conductor, worker, knowledge, research and heartbeat specs read
        # as ``builtin`` in discovery; the sync stamped their rows the same way.
        names = [
            "kirocrew-conductor",
            "kirocrew-worker",
            "kirocrew-knowledge",
            "kirocrew-research",
            "kirocrew-heartbeat",
        ]
        _seed(**{name: _synced(name) for name in names})
        report = _run(log)
        assert set(report.removed) == set(names)
        assert report.refused == []
        assert not set(names) & KiroCrewConfig.load().agents.keys()

    def test_a_renamed_row_is_removed(self, bindings_dir, log):
        # A row whose name is not its binding is judged like any other.
        _seed(renamed=_synced("radar"))
        report = _run(log)
        assert report.removed == ["renamed"]

    def test_a_kirocrew_stamped_row_bound_to_the_owners_agent_is_kept(self, bindings_dir, log):
        # What the dashboard wrote before member_id existed: kept whatever
        # else was edited on it, and with no DM turn at all.
        from kiro_crew.config.loader import update_config_locked

        _seed(
            mine=_owner("mine"),
            tuned=_owner("tuned", model="m", starred=True),
            renamed=_owner("renamed", kiro_agent="scout"),
            unstamped=_owner("unstamped"),
        )

        def _drop_source_key(doc):
            del doc["agents"]["unstamped"]["source"]
            return doc

        update_config_locked(mutate=_drop_source_key)
        report = _run(log)
        assert report.removed == [] and report.refused == [] and report.kept == []
        assert {"mine", "tuned", "renamed", "unstamped"} <= KiroCrewConfig.load().agents.keys()
        assert mig.marker_path().exists()

    @pytest.mark.parametrize("core", sorted(mig.CORE_RUNTIME_AGENT_NAMES))
    def test_a_kirocrew_row_bound_to_a_core_runtime_spec_is_removed(self, bindings_dir, log, core):
        _seed(twin=_owner("twin", kiro_agent=core))
        report = _run(log)
        assert report.removed == ["twin"]
        assert "twin" not in KiroCrewConfig.load().agents

    def test_the_core_runtime_names_are_the_three_kirocrew_stamped_specs(self):
        from kiro_crew.agent_files import (
            AGENT_FILENAME,
            GUEST_AGENT_FILENAME,
            LITE_AGENT_FILENAME,
            OWNED_KIRO_AGENT_FILES,
        )

        assert mig.CORE_RUNTIME_AGENT_NAMES == {"kirocrew", "kirocrew-lite", "kirocrew-guest"}
        assert mig.CORE_RUNTIME_AGENT_NAMES == {
            Path(f).stem for f in (AGENT_FILENAME, LITE_AGENT_FILENAME, GUEST_AGENT_FILENAME)
        }
        # The other runtime-owned specs read as ``builtin`` in discovery and
        # are not in the set: a kirocrew-stamped row bound to one is kept.
        assert "kirocrew-conductor" in {Path(f).stem for f in OWNED_KIRO_AGENT_FILES}
        assert "kirocrew-conductor" not in mig.CORE_RUNTIME_AGENT_NAMES

    def test_the_default_row_is_never_a_candidate(self, bindings_dir, log):
        cfg = KiroCrewConfig.load()
        cfg.agents["primary"] = _synced("primary")
        cfg.default_agent = "primary"
        cfg.save()
        assert KiroCrewConfig.load().default_agent == "primary"
        report = _run(log)
        assert report.removed == []
        assert {"default", "primary"} <= KiroCrewConfig.load().agents.keys()

    def test_rows_bound_to_skill_view_aliases_are_removed(self, bindings_dir, log):
        # The older sync wrote one row per ``kirocrew-skill-view-*`` alias file;
        # they are ordinary rows under the rule and go with the rest.
        a = "kirocrew-skill-view-000d98d7ce0f52f0500da355"
        b = "kirocrew-skill-view-0245aff77b1473735250d2a0"
        cfg = KiroCrewConfig.load()
        cfg.save()
        raw = json.loads(config_path().read_text())
        raw["agents"][a] = {
            "member_id": "",
            "kiro_agent": a,
            "workspace": "default",
            "memory_store": "default",
            "model": "",
            "display_name": "",
            "description": "One-click setup agent -- managed by AIM.",
            "triggers": "",
            "source": "builtin",
            "starred": False,
        }
        raw["agents"][b] = {
            "member_id": "",
            "kiro_agent": b,
            "workspace": "default",
            "memory_store": "default",
            "description": "Communication agent -- managed by AIM.",
            "triggers": "",
            "source": "builtin",
            "starred": False,
        }
        config_path().write_text(json.dumps(raw))
        report = _run(log)
        assert set(report.removed) == {a, b}
        assert not {a, b} & KiroCrewConfig.load().agents.keys()

    def test_a_skill_view_row_the_owner_chatted_with_or_claimed_is_kept(self, bindings_dir, log):
        chatted = "kirocrew-skill-view-" + "1" * 24
        created = "kirocrew-skill-view-" + "2" * 24
        tuned = "kirocrew-skill-view-" + "3" * 24
        stamped = "kirocrew-skill-view-" + "4" * 24
        plain = "kirocrew-skill-view-" + "5" * 24
        cfg = KiroCrewConfig.load()
        for name in (chatted, created, tuned, plain):
            cfg.agents[name] = _synced(name)
        cfg.agents[created].member_id = "member-created"
        cfg.agents[tuned].model = "m"
        cfg.agents[stamped] = _synced(stamped, source="kirocrew")
        cfg.save()
        log.dm(chatted)
        report = _run(log)
        assert set(report.removed) == {tuned, plain} and report.refused == []
        assert {chatted, created, stamped} <= KiroCrewConfig.load().agents.keys()

    def test_a_created_crewmate_is_never_removed(self, bindings_dir, log):
        cfg = KiroCrewConfig.load()
        cfg.agents["created"] = _synced("created")
        cfg.agents["created"].member_id = "member-created"
        cfg.save()
        report = _run(log)
        assert report.removed == []
        assert KiroCrewConfig.load().agents["created"].member_id == "member-created"

    def test_second_boot_is_a_no_op(self, old_style_config, bindings_dir, log):
        log.dm("radar")
        _run(log)
        _seed(late=_synced("late"))
        report = _run(log)
        assert report.skipped_marker is True
        assert "late" in KiroCrewConfig.load().agents

    def test_the_earlier_passes_markers_do_not_stop_this_one(
        self, old_style_config, bindings_dir, log
    ):
        from kiro_crew.config.paths import config_dir

        earlier = [
            config_dir() / "crewmate_prune_migrated.json",
            config_dir() / "crewmate_prune_v2_migrated.json",
            config_dir() / "crewmate_prune_v3_migrated.json",
        ]
        for marker in earlier:
            marker.write_text(json.dumps({"removed": [], "kept": [], "doubted": {}}))
        report = _run(log)
        assert report.skipped_marker is False
        assert set(report.removed) == {"radar", "scout"}
        assert all(marker.exists() for marker in earlier)
        assert mig.marker_path().name == "crewmate_prune_v4_migrated.json"

    def test_a_no_op_pass_still_writes_the_marker(self, bindings_dir, log):
        report = _run(log)
        assert report.removed == [] and report.kept == []
        assert mig.marker_path().exists()


class TestKeptOnDoubt:
    """A candidate whose binding or thread transcript is there but cannot be
    judged is kept; the others are judged on their own evidence. No
    conversation log keeps everyone. The pass still finishes and the marker
    names the doubted, so no boot re-runs it."""

    def test_an_archive_directory_that_cannot_be_listed_keeps_that_crewmate(
        self, old_style_config, bindings_dir, log, monkeypatch
    ):
        log.dm("radar")
        archive = log._dir / "archive"
        archive.mkdir()
        real_scandir = os.scandir

        def _unreadable(path):
            if isinstance(path, (str, os.PathLike)) and Path(path) == archive:
                raise PermissionError("archive unreadable")
            return real_scandir(path)

        monkeypatch.setattr(mig.os, "scandir", _unreadable)
        report = _run(log)

        assert report.removed == []
        assert report.kept == ["radar"]
        assert list(report.doubted) == ["scout"]
        assert "could not list archived transcripts" in report.doubted["scout"]
        assert "scout" in KiroCrewConfig.load().agents
        assert json.loads(mig.marker_path().read_text())["doubted"] == report.doubted

    def test_a_listed_archive_segment_that_disappears_keeps_that_crewmate(
        self, old_style_config, bindings_dir, log, monkeypatch
    ):
        segment = log.dm("scout", archived="20260901-195926")
        real_open = mig.open_file_no_reparse

        def _remove_then_open(path, *, nonblocking=False):
            if Path(path) == segment:
                segment.unlink()
            return real_open(path, nonblocking=nonblocking)

        monkeypatch.setattr(mig, "open_file_no_reparse", _remove_then_open)
        report = _run(log)

        assert report.removed == ["radar"]
        assert list(report.doubted) == ["scout"]
        assert "disappeared before it could be opened" in report.doubted["scout"]
        assert "scout" in KiroCrewConfig.load().agents
        assert json.loads(mig.marker_path().read_text())["doubted"] == report.doubted

    def test_a_thread_whose_first_line_does_not_parse_keeps_that_crewmate(
        self, old_style_config, bindings_dir, log
    ):
        log.dm("scout", raw=b"{not json\n")
        report = _run(log)
        assert report.removed == ["radar"]
        assert list(report.doubted) == ["scout"]
        assert "scout" in KiroCrewConfig.load().agents
        assert json.loads(mig.marker_path().read_text())["doubted"] == report.doubted

    def test_a_thread_that_is_not_utf8_keeps_that_crewmate(
        self, old_style_config, bindings_dir, log
    ):
        log.dm("scout", raw=b"\xff\xfe\n")
        report = _run(log)
        assert list(report.doubted) == ["scout"]

    def test_an_empty_thread_is_a_torn_write_and_keeps_that_crewmate(
        self, old_style_config, bindings_dir, log
    ):
        log.dm("scout", raw=b"")
        report = _run(log)
        assert list(report.doubted) == ["scout"]
        assert "empty" in report.doubted["scout"]

    def test_a_first_line_over_budget_keeps_that_crewmate(
        self, old_style_config, bindings_dir, log, monkeypatch
    ):
        monkeypatch.setattr(mig, "_TRANSCRIPT_META_LINE_MAX", 16)
        log.dm("scout")
        report = _run(log)
        assert list(report.doubted) == ["scout"]

    def test_a_torn_row_after_the_metadata_still_counts(self, old_style_config, bindings_dir, log):
        # A row that was being written proves a turn was sent.
        log.dm("scout", raw=_META.encode() + b'{"role": "us')
        report = _run(log)
        assert report.kept == ["scout"]

    def test_a_row_past_the_read_cap_still_counts(
        self, old_style_config, bindings_dir, log, monkeypatch
    ):
        # A row too long to buffer is still a written turn, never "no turn".
        monkeypatch.setattr(mig, "_TRANSCRIPT_META_LINE_MAX", len(_META) + 8)
        log.dm(
            "scout", raw=_META.encode() + b'{"role": "user", "content": "' + b"x" * 256 + b'"}\n'
        )
        report = _run(log)
        assert report.kept == ["scout"]

    @pytest.mark.skipif(
        sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
        reason="POSIX permission bits; root reads any file",
    )
    def test_a_thread_that_cannot_be_opened_keeps_that_crewmate(
        self, old_style_config, bindings_dir, log
    ):
        path = log.dm("scout")
        path.chmod(0)
        try:
            report = _run(log)
        finally:
            path.chmod(0o644)
        assert list(report.doubted) == ["scout"]
        assert report.removed == ["radar"]

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO")
    def test_a_fifo_at_a_thread_path_is_no_record_and_no_hang(
        self, old_style_config, bindings_dir, log
    ):
        os.mkfifo(log._dir / "dashboard_member-scout.jsonl")
        report = _run(log)  # returns: the open does not wait
        assert set(report.removed) == {"radar", "scout"}

    @pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks")
    def test_a_link_at_a_thread_path_is_no_record(
        self, old_style_config, bindings_dir, log, tmp_path
    ):
        real = tmp_path / "elsewhere.jsonl"
        real.write_text(_META + _TURN)
        os.symlink(real, log._dir / "dashboard_member-scout.jsonl")
        report = _run(log)
        assert "scout" in report.removed

    def test_a_doubted_pass_is_recorded_and_not_re_run(self, old_style_config, bindings_dir, log):
        bindings_dir("scout", raw=b"{not json")
        report = _run(log)
        assert list(report.doubted) == ["scout"]
        assert "scout" in KiroCrewConfig.load().agents
        report = _run(log)
        assert report.skipped_marker is True

    def test_no_conversation_log_keeps_every_candidate(self, old_style_config, bindings_dir):
        report = _run(None)
        assert report.removed == []
        assert set(report.doubted) == {"radar", "scout"}
        assert "scout" in KiroCrewConfig.load().agents
        assert mig.marker_path().exists()

    def test_a_malformed_binding_file_keeps_that_crewmate(
        self, old_style_config, bindings_dir, log
    ):
        # The roster's own reader answers "not bound" for this file; the prune
        # must not: the binding may record the thread's slot key.
        bindings_dir("scout", raw=b"{not json")
        report = _run(log)
        assert report.removed == ["radar"]
        assert list(report.doubted) == ["scout"]
        assert "scout" in KiroCrewConfig.load().agents
        assert json.loads(mig.marker_path().read_text())["doubted"] == report.doubted

    def test_an_unreadable_binding_file_keeps_that_crewmate(
        self, old_style_config, bindings_dir, log
    ):
        bindings_dir("scout", raw=b"\xff\xfe")  # not UTF-8
        report = _run(log)
        assert list(report.doubted) == ["scout"]
        assert "scout" in KiroCrewConfig.load().agents

    def test_an_unresolvable_binding_path_keeps_the_crewmate(
        self, old_style_config, bindings_dir, monkeypatch, log
    ):
        def _boom(slug):
            raise OSError("members root unreadable")

        monkeypatch.setattr("kiro_crew.members.dm_binding_path", _boom)
        report = _run(log)
        assert set(report.doubted) == {"radar", "scout"}
        assert "scout" in KiroCrewConfig.load().agents

    def test_a_binding_path_refused_by_containment_keeps_the_crewmate(
        self, old_style_config, bindings_dir, monkeypatch, log
    ):
        # A slug that passed ``member_slug`` but whose binding path resolves
        # outside the trust root (a symlinked component) is a binding that MAY
        # exist, not one that does not.
        from kiro_crew.members import MemberSlugError

        def _escapes(slug):
            raise MemberSlugError(f"member slug {slug!r} escapes root")

        monkeypatch.setattr("kiro_crew.members.dm_binding_path", _escapes)
        report = _run(log)
        assert set(report.doubted) == {"radar", "scout"}
        assert "scout" in KiroCrewConfig.load().agents

    def test_a_doubt_on_one_candidate_does_not_spare_the_next(
        self, old_style_config, bindings_dir, log
    ):
        # Check-then-delete is per candidate: radar's binding is in doubt and
        # radar stays; scout's evidence is clean and scout is judged on it.
        bindings_dir("radar", raw=b"{not json")
        report = _run(log)
        assert list(report.doubted) == ["radar"]
        assert report.removed == ["scout"]
        after = KiroCrewConfig.load()
        assert "radar" in after.agents and "scout" not in after.agents

    def test_a_member_id_assigned_under_the_lock_is_refused_and_no_marker(
        self, old_style_config, bindings_dir, log
    ):
        log.dm("radar")
        # A member-aware writer stamped the row between the judgement and the
        # lock: newer evidence wins, the delete is refused, and a refusal is
        # not a commit -- no marker, so the next boot re-judges it.
        original = mig.remove_never_chatted

        def _claim_then_remove(cfg_, names, **kw):
            live = KiroCrewConfig.load()
            live.agents["scout"].member_id = "member-scout"
            live.save()
            return original(cfg_, names, **kw)

        with patch.object(mig, "remove_never_chatted", _claim_then_remove):
            report = _run(log)
        assert report.removed == []
        assert report.refused == ["scout"]
        assert KiroCrewConfig.load().agents["scout"].member_id == "member-scout"
        assert not mig.marker_path().exists()

    def test_a_rebinding_onto_a_kept_kirocrew_row_under_the_lock_is_refused(
        self, old_style_config, bindings_dir, log
    ):
        log.dm("radar")
        original = mig.remove_never_chatted

        def _restamp_then_remove(cfg_, names, **kw):
            live = KiroCrewConfig.load()
            live.agents["scout"].source = "kirocrew"
            live.save()
            return original(cfg_, names, **kw)

        with patch.object(mig, "remove_never_chatted", _restamp_then_remove):
            report = _run(log)
        assert report.refused == ["scout"]
        assert "scout" in KiroCrewConfig.load().agents
        assert not mig.marker_path().exists()

    def test_a_row_restamped_by_an_app_under_the_lock_is_refused(
        self, old_style_config, bindings_dir, log
    ):
        log.dm("radar")
        original = mig.remove_never_chatted

        def _app_claims_then_remove(cfg_, names, **kw):
            live = KiroCrewConfig.load()
            live.agents["scout"].source = "auto-improvement"
            live.save()
            return original(cfg_, names, **kw)

        with patch.object(mig, "remove_never_chatted", _app_claims_then_remove):
            report = _run(log)
        assert report.refused == ["scout"]
        assert KiroCrewConfig.load().agents["scout"].source == "auto-improvement"
        assert not mig.marker_path().exists()

    def test_an_edit_under_the_lock_does_not_refuse(self, old_style_config, bindings_dir, log):
        # A model pinned between the judgement and the lock is not a claim on
        # the crewmate; the delete goes through.
        log.dm("radar")
        original = mig.remove_never_chatted

        def _edit_then_remove(cfg_, names, **kw):
            live = KiroCrewConfig.load()
            live.agents["scout"].model = "some-model"
            live.save()
            return original(cfg_, names, **kw)

        with patch.object(mig, "remove_never_chatted", _edit_then_remove):
            report = _run(log)
        assert report.removed == ["scout"] and report.refused == []
        assert mig.marker_path().exists()

    def test_a_row_made_the_default_under_the_lock_is_refused(
        self, old_style_config, bindings_dir, log
    ):
        log.dm("radar")
        original = mig.remove_never_chatted

        def _promote_then_remove(cfg_, names, **kw):
            live = KiroCrewConfig.load()
            live.default_agent = "scout"
            live.save()
            return original(cfg_, names, **kw)

        with patch.object(mig, "remove_never_chatted", _promote_then_remove):
            report = _run(log)
        assert report.refused == ["scout"]
        assert KiroCrewConfig.load().default_agent == "scout"
        assert not mig.marker_path().exists()

    def test_a_legacy_row_with_fewer_keys_is_still_removed(
        self, old_style_config, bindings_dir, log
    ):
        # A build whose record had no member_id wrote rows without that key;
        # the missing key reads as its default and the row is judged the same.
        from kiro_crew.config.loader import read_config_for_update, update_config_locked

        def _strip(doc):
            row = doc["agents"]["scout"]
            for key in ("member_id", "starred", "session_color", "avatar", "reasoning_effort"):
                row.pop(key, None)
            return doc

        update_config_locked(mutate=_strip)
        assert "member_id" not in read_config_for_update()["agents"]["scout"]
        log.dm("radar")
        report = _run(log)
        assert report.removed == ["scout"]
        assert mig.marker_path().exists()


class TestAbandoned:
    """The gateway's stop signal: a pass told to stop keeps what it has not
    judged, still writes its marker, and never deletes past the check inside
    the config lock."""

    def test_a_pass_told_to_stop_before_judging_keeps_everyone_and_records_it(
        self, old_style_config, bindings_dir, log
    ):
        report = _run(log, abandoned=lambda: True)
        assert report.removed == [] and report.kept == []
        assert report.doubted == {
            "radar": mig.ABANDONED_REASON,
            "scout": mig.ABANDONED_REASON,
        }
        after = KiroCrewConfig.load()
        assert "radar" in after.agents and "scout" in after.agents
        assert json.loads(mig.marker_path().read_text())["doubted"] == report.doubted
        assert _run(log).skipped_marker is True

    def test_a_stop_that_lands_inside_the_lock_leaves_the_row(self, old_style_config, log):
        # ``remove_never_chatted`` polls twice: once before the row, once inside
        # the config lock right before the delete. The signal lands between the
        # two, which is the last place a delete could still happen.
        cfg = KiroCrewConfig.load()
        polls = 0

        def _second_poll_says_stop() -> bool:
            nonlocal polls
            polls += 1
            return polls >= 2

        removed, refused, left = mig.remove_never_chatted(
            cfg, ["scout"], abandoned=_second_poll_says_stop
        )
        assert (removed, refused, left) == ([], [], ["scout"])
        assert polls == 2
        assert "scout" in KiroCrewConfig.load().agents

    def test_a_stop_during_the_pass_keeps_the_rest_and_writes_the_marker(
        self, old_style_config, bindings_dir, log
    ):
        import threading

        log.dm("radar")
        stop = threading.Event()
        original = mig.remove_never_chatted

        def _stop_then_remove(cfg_, names, **kw):
            stop.set()
            return original(cfg_, names, **kw)

        with patch.object(mig, "remove_never_chatted", _stop_then_remove):
            report = _run(log, abandoned=stop.is_set)
        assert report.kept == ["radar"]
        assert report.removed == [] and report.refused == []
        assert report.doubted == {"scout": mig.ABANDONED_REASON}
        assert "scout" in KiroCrewConfig.load().agents
        assert mig.marker_path().exists()


class TestCandidates:
    def test_every_row_the_rule_does_not_keep_is_a_candidate(self):
        cfg = KiroCrewConfig.load()
        cfg.agents["radar"] = _synced("radar")
        cfg.agents["omni"] = _synced("omni", source="package")
        cfg.agents["older"] = _synced("older", source="aim")
        cfg.agents["kirocrew-conductor"] = _synced("kirocrew-conductor")
        cfg.agents["copy"] = KiroCrewAgentConfig(kiro_agent="copy", source="builtin")
        cfg.agents["tuned"] = KiroCrewAgentConfig(kiro_agent="tuned", source="builtin", model="m")
        cfg.agents["routed"] = KiroCrewAgentConfig(
            kiro_agent="routed", source="builtin", triggers="x"
        )
        cfg.agents["renamed"] = _synced("radar")
        cfg.agents["twin"] = _owner("twin", kiro_agent="kirocrew")
        # Kept on the row alone: a created crewmate, a kirocrew-stamped row
        # bound to an agent of the owner's own, and an app's own stamp.
        cfg.agents["created"] = _synced("created")
        cfg.agents["created"].member_id = "member-created"
        cfg.agents["mine"] = _owner("mine")
        cfg.agents["odd"] = KiroCrewAgentConfig(kiro_agent="odd", source="somewhere")
        cfg.save()
        raw = mig._raw_agents_section()
        got = mig._removal_candidates(cfg, raw, {}, frozenset())
        assert got == [
            "radar",
            "omni",
            "older",
            "kirocrew-conductor",
            "copy",
            "tuned",
            "routed",
            "renamed",
            "twin",
        ]

    def test_the_default_row_and_default_agent_are_not_candidates(self):
        cfg = KiroCrewConfig.load()
        cfg.agents["primary"] = _synced("primary")
        cfg.agents["scout"] = _synced("scout")
        cfg.default_agent = "primary"
        cfg.save()
        cfg = KiroCrewConfig.load()
        assert mig._removal_candidates(cfg, mig._raw_agents_section(), {}, frozenset()) == ["scout"]

    def test_a_row_that_is_not_a_record_is_left_alone(self):
        from kiro_crew.config.loader import update_config_locked

        cfg = KiroCrewConfig.load()
        cfg.agents["scout"] = _synced("scout")
        cfg.save()

        def _flatten(doc):
            doc["agents"]["scout"] = "scout"
            return doc

        update_config_locked(mutate=_flatten)
        assert mig._removal_candidates(cfg, mig._raw_agents_section(), {}, frozenset()) == []

    def test_a_kirocrew_row_is_kept(self, log):
        _seed(runtime=_owner("runtime"))
        report = _run(log)
        assert report.removed == [] and report.refused == []
        assert "runtime" in KiroCrewConfig.load().agents
        assert mig.marker_path().exists()

    def test_a_row_bound_to_a_runtime_owned_builtin_spec_is_removed(self, bindings_dir, log):
        _seed(**{"kirocrew-conductor": _synced("kirocrew-conductor"), "scout": _synced("scout")})
        report = _run(log)
        assert set(report.removed) == {"kirocrew-conductor", "scout"} and report.refused == []
        assert "kirocrew-conductor" not in KiroCrewConfig.load().agents
        assert mig.marker_path().exists()

    def test_the_spec_is_never_consulted(self, old_style_config, bindings_dir, log, _agents_dir):
        # The bound spec file is not read at the scan or under the lock: a
        # spec that appears, changes or is one of the runtime's own files
        # neither keeps nor refuses.
        (_agents_dir / "kirocrew-conductor.json").write_text(
            json.dumps({"name": "scout", "description": "now the conductor"})
        )
        original = mig.remove_never_chatted

        def _replace_spec_then_remove(cfg_, names, **kw):
            (_agents_dir / "scout.json").write_text(json.dumps({"name": "renamed-scout"}))
            return original(cfg_, names, **kw)

        with patch.object(mig, "remove_never_chatted", _replace_spec_then_remove):
            report = _run(log)
        assert set(report.removed) == {"radar", "scout"} and report.refused == []
        assert mig.marker_path().exists()

    def test_a_row_the_overlay_touches_is_never_a_candidate(self):
        # ``kirocrew config set --local agents.radar.model m`` leaves the base
        # row pristine and puts one leaf in config.local.json; deleting the
        # base row would leave that leaf as a crewmate bound to nothing.
        from kiro_crew.config.loader import config_local_path

        cfg = KiroCrewConfig.load()
        cfg.agents["radar"] = _synced("radar")
        cfg.agents["scout"] = _synced("scout")
        cfg.save()
        config_local_path().write_text(json.dumps({"agents": {"radar": {"model": "m"}}}))
        cfg = KiroCrewConfig.load()
        raw = mig._raw_agents_section()
        overlay = mig._raw_agents_section(config_local_path())
        assert overlay == {"radar": {"model": "m"}}
        assert mig._removal_candidates(cfg, raw, overlay, frozenset()) == ["scout"]

    def test_an_overlay_leaf_appearing_under_the_lock_refuses_the_delete(
        self, old_style_config, bindings_dir, log
    ):
        from kiro_crew.config.loader import config_local_path

        original = mig.remove_never_chatted

        def _overlay_then_remove(cfg_, names, **kw):
            config_local_path().write_text(json.dumps({"agents": {"scout": {"model": "m"}}}))
            return original(cfg_, names, **kw)

        with patch.object(mig, "remove_never_chatted", _overlay_then_remove):
            report = _run(log)
        assert report.refused == ["scout"]
        assert "scout" in KiroCrewConfig.load().agents
        assert not mig.marker_path().exists()

    def test_a_teamed_row_is_never_a_candidate(self):
        # Placing a crewmate on a team is the owner's own act, so the row is
        # the owner's whatever its shape.
        from kiro_crew import crew_teams

        cfg = KiroCrewConfig.load()
        cfg.agents["radar"] = _synced("radar")
        cfg.agents["scout"] = _synced("scout")
        cfg.save()
        crew_teams.create_team("ops", ["radar"], known=lambda: {"radar", "scout"})
        teamed = mig._teamed_names()
        assert teamed == frozenset({"radar"})
        assert mig._removal_candidates(cfg, mig._raw_agents_section(), {}, teamed) == ["scout"]

    def test_a_teamed_never_chatted_generated_row_is_kept(
        self, old_style_config, bindings_dir, log
    ):
        # Neither removed, refused nor doubted: not a candidate at all, so the
        # pass records itself as complete with the row in place.
        from kiro_crew import crew_teams

        crew_teams.create_team("ops", ["scout"], known=lambda: {"radar", "scout"})
        report = _run(log)
        assert report.removed == ["radar"]
        assert report.refused == [] and report.doubted == {}
        assert "scout" in KiroCrewConfig.load().agents
        assert [t.members for t in crew_teams.read_teams()] == [["scout"]]
        assert mig.marker_path().exists()

    def test_an_unteamed_row_is_still_removed_when_teams_exist(
        self, old_style_config, bindings_dir, log
    ):
        from kiro_crew import crew_teams

        crew_teams.create_team("ops", ["by-hand"], known=lambda: {"by-hand"})
        report = _run(log)
        assert set(report.removed) == {"radar", "scout"}
        assert "scout" not in KiroCrewConfig.load().agents
        assert mig.marker_path().exists()

    def test_a_row_teamed_under_the_lock_refuses_the_delete(
        self, old_style_config, bindings_dir, log
    ):
        # The team write lands between discovery and the delete: newer
        # evidence, the delete is refused, no marker, the row and its team stay.
        from kiro_crew import crew_teams

        original = mig.remove_never_chatted

        def _team_then_remove(cfg_, names, **kw):
            # Called once per candidate; the team is made on the first call.
            if not crew_teams.read_teams():
                crew_teams.create_team("ops", ["scout"], known=lambda: {"radar", "scout"})
            return original(cfg_, names, **kw)

        with patch.object(mig, "remove_never_chatted", _team_then_remove):
            report = _run(log)
        assert report.removed == ["radar"]
        assert report.refused == ["scout"]
        assert "scout" in KiroCrewConfig.load().agents
        assert [t.members for t in crew_teams.read_teams()] == [["scout"]]
        assert not mig.marker_path().exists()

    def test_an_unreadable_team_document_keeps_every_candidate(
        self, old_style_config, bindings_dir, log
    ):
        # None of them can be shown to be off a team, so all are kept on doubt
        # -- the way no conversation log keeps them -- and the marker records it.
        from kiro_crew import crew_teams

        path = crew_teams.teams_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        report = _run(log)
        assert report.removed == [] and report.refused == []
        assert set(report.doubted) == {"radar", "scout"}
        assert all(mig.TEAMS_UNREADABLE_REASON in why for why in report.doubted.values())
        assert {"radar", "scout"} <= KiroCrewConfig.load().agents.keys()
        assert json.loads(mig.marker_path().read_text())["doubted"] == report.doubted

    def test_a_team_document_turned_unreadable_under_the_lock_refuses_the_delete(
        self, old_style_config, bindings_dir, log
    ):
        from kiro_crew import crew_teams

        original = mig.remove_never_chatted

        def _corrupt_then_remove(cfg_, names, **kw):
            path = crew_teams.teams_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{not json")
            return original(cfg_, names, **kw)

        with patch.object(mig, "remove_never_chatted", _corrupt_then_remove):
            report = _run(log)
        assert set(report.refused) == {"radar", "scout"}
        assert not mig.marker_path().exists()

    def test_the_team_lock_is_held_until_the_base_delete_has_committed(
        self, old_style_config, bindings_dir, log
    ):
        # Same rule as the overlay lock: at the moment the team document lock
        # is released the base file has already lost the row, so a team write
        # that waited on it cannot place a name the pass is about to delete.
        from kiro_crew.config.loader import config_path

        log.dm("radar")
        base_at_release: list[dict] = []
        real = mig.document_lock

        @contextlib.contextmanager
        def _recording(directory=None):
            with real(directory):
                yield
                base_at_release.append(
                    json.loads(config_path().read_text(encoding="utf-8")).get("agents", {})
                )

        with patch.object(mig, "document_lock", _recording):
            report = _run(log)
        assert report.removed == ["scout"]
        assert len(base_at_release) == 1
        assert "scout" not in base_at_release[0]
        assert "radar" in base_at_release[0]

    def test_the_overlay_lock_is_held_until_the_base_delete_has_committed(
        self, old_style_config, bindings_dir, log
    ):
        # The overlay's sidecar lock is released after the base write, not
        # after the peek: at the moment it is released, the base file on disk
        # has already lost the row. An overlay writer that waited on the lock
        # therefore finds the row gone, never a row it can still bind to.
        from kiro_crew.config.loader import config_path

        log.dm("radar")
        base_at_release: list[dict] = []
        real = mig._config_write_lock

        @contextlib.contextmanager
        def _recording(p, **kw):
            with real(p, **kw):
                yield
                base_at_release.append(
                    json.loads(config_path().read_text(encoding="utf-8")).get("agents", {})
                )

        with patch.object(mig, "_config_write_lock", _recording):
            report = _run(log)
        assert report.removed == ["scout"]
        # One overlay hold per delete; the base row was gone when it ended.
        assert len(base_at_release) == 1
        assert "scout" not in base_at_release[0]
        assert "radar" in base_at_release[0]

    def test_the_spec_lock_is_not_taken(self, old_style_config, bindings_dir, log):
        # Nothing about the bound spec is a keep condition, so the pass holds
        # no spec lock: a lock it never takes cannot invert any writer's order.
        import kiro_crew.agent as agent_mod

        log.dm("radar")
        holds: list[Path] = []
        real = agent_mod.agents_spec_lock

        @contextlib.contextmanager
        def _recording(agents_dir):
            holds.append(Path(agents_dir))
            with real(agents_dir):
                yield

        with patch.object(agent_mod, "agents_spec_lock", _recording):
            report = _run(log)
        assert report.removed == ["scout"]
        assert holds == []

    def test_the_locks_nest_base_then_overlay_then_teams(self, old_style_config, bindings_dir, log):
        # The order every binding writer keeps, then the team document lock
        # innermost (its own contract is registry lock first, then it, and
        # nothing takes a registry or overlay lock while holding it), so no
        # writer that holds the base lock can wait on this pass for a lock
        # this pass waits on it for.
        log.dm("radar")
        order: list[str] = []
        real_overlay = mig._config_write_lock
        real_teams = mig.document_lock

        @contextlib.contextmanager
        def _overlay(p, **kw):
            with real_overlay(p, **kw):
                order.append("overlay")
                yield

        @contextlib.contextmanager
        def _teams(directory=None):
            with real_teams(directory):
                order.append("teams")
                yield

        with (
            patch.object(mig, "_config_write_lock", _overlay),
            patch.object(mig, "document_lock", _teams),
        ):
            report = _run(log)
        assert report.removed == ["scout"]
        assert order == ["overlay", "teams"]


class TestTheRowRule:
    """``_is_owners_row``: the part of the keep rule the raw row alone answers."""

    def test_a_member_id_keeps(self):
        assert mig._is_owners_row({"kiro_agent": "radar", "source": "builtin", "member_id": "m1"})
        # A member_id that is not even a string is doubt about a created
        # crewmate, and doubt keeps.
        assert mig._is_owners_row({"kiro_agent": "radar", "source": "builtin", "member_id": ["m"]})
        assert not mig._is_owners_row({"kiro_agent": "radar", "source": "builtin", "member_id": ""})

    def test_a_kirocrew_stamp_off_the_core_specs_keeps(self):
        assert mig._is_owners_row({"kiro_agent": "radar", "source": "kirocrew"})
        assert mig._is_owners_row({"kiro_agent": "kirocrew-conductor", "source": "kirocrew"})
        # A missing source key reads as the record's default, ``kirocrew``.
        assert mig._is_owners_row({"kiro_agent": "radar"})
        assert mig._is_owners_row({"kiro_agent": "radar", "model": "m", "starred": True})

    @pytest.mark.parametrize("core", sorted(mig.CORE_RUNTIME_AGENT_NAMES))
    def test_a_kirocrew_stamp_on_a_core_spec_does_not_keep(self, core):
        assert not mig._is_owners_row({"kiro_agent": core, "source": "kirocrew"})
        assert not mig._is_owners_row({"kiro_agent": core})

    @pytest.mark.parametrize("source", sorted(mig.SYNC_ROW_SOURCES))
    def test_a_sync_source_does_not_keep(self, source):
        assert not mig._is_owners_row({"kiro_agent": "radar", "source": source})
        # Edits do not change that.
        assert not mig._is_owners_row(
            {"kiro_agent": "radar", "source": source, "model": "m", "avatar": {"kind": "image"}}
        )

    @pytest.mark.parametrize(
        "source", ["auto-improvement", "somewhere", "Builtin", "packages", "", [], 5, None]
    )
    def test_any_other_source_keeps(self, source):
        assert mig._is_owners_row({"kiro_agent": "radar", "source": source})
        # Bound to a core runtime spec too: only ``kirocrew`` is judged on that.
        assert mig._is_owners_row({"kiro_agent": "kirocrew", "source": source})

    def test_the_sync_sources_are_exactly_the_retired_syncs_stamps(self):
        assert mig.SYNC_ROW_SOURCES == {"builtin", "package", "aim"}

    def test_a_missing_source_reads_as_the_loaders_default(self):
        from kiro_crew.config.loader import update_config_locked

        cfg = KiroCrewConfig.load()
        cfg.agents["bare"] = _synced("bare")
        cfg.save()

        def _drop(doc):
            del doc["agents"]["bare"]["source"]
            return doc

        update_config_locked(mutate=_drop)
        assert KiroCrewConfig.load().agents["bare"].source == mig.OWNER_ROW_SOURCE == "kirocrew"


class TestTheGate:
    """``_register_crewmate_prune_gate``: armed before bind, holds writers only.

    A stub ``state`` carrying just the event stands in for ``DashboardState``;
    the gate reads nothing else. The timeout is patched down so the 503 path
    runs in milliseconds.
    """

    @staticmethod
    def _app():
        import asyncio
        from types import SimpleNamespace

        from aiohttp import web

        from kiro_crew.dashboard import server as server_mod

        event = asyncio.Event()
        event.set()
        state = SimpleNamespace(crewmate_prune_settled=event)
        app = web.Application()
        hits: list[str] = []

        async def _write(request):
            hits.append(request.method)
            return web.json_response({"ok": True})

        app.router.add_post("/api/chat/slots/s1/agent", _write)
        app.router.add_get("/api/chat/slots", _write)
        app.router.add_post("/v1/chat/completions", _write)
        app.router.add_get("/api/members", _write)
        app.router.add_get("/api/members/{slug}/activity", _write)
        app.router.add_get("/api/membersx", _write)
        server_mod._register_crewmate_prune_gate(app, state)
        return app, state, hits

    def test_registering_arms_the_barrier(self):
        _app, state, _hits = self._app()
        assert not state.crewmate_prune_settled.is_set()

    @pytest.mark.asyncio
    async def test_a_mutating_api_request_waits_until_the_pass_settles(self):
        import asyncio

        from aiohttp.test_utils import TestClient, TestServer

        app, state, hits = self._app()
        async with TestClient(TestServer(app)) as client:
            pending = asyncio.ensure_future(client.post("/api/chat/slots/s1/agent"))
            await asyncio.sleep(0.05)
            assert hits == []  # held, not refused
            state.crewmate_prune_settled.set()
            resp = await pending
            assert resp.status == 200
            assert hits == ["POST"]

    @pytest.mark.asyncio
    async def test_reads_are_never_held(self):
        from aiohttp.test_utils import TestClient, TestServer

        app, _state, hits = self._app()
        async with TestClient(TestServer(app)) as client:
            assert (await client.get("/api/chat/slots")).status == 200
        assert hits == ["GET"]

    @pytest.mark.asyncio
    async def test_the_member_roster_read_is_held_too(self):
        # ``GET /api/members`` folds and retires a member's legacy activity file
        # and appends to the member log -- the places the prune reads -- so it
        # waits like a write, and so does every route under the prefix. A path
        # that merely shares the letters does not.
        import asyncio

        from aiohttp.test_utils import TestClient, TestServer

        app, state, hits = self._app()
        async with TestClient(TestServer(app)) as client:
            roster = asyncio.ensure_future(client.get("/api/members"))
            activity = asyncio.ensure_future(client.get("/api/members/radar/activity"))
            await asyncio.sleep(0.05)
            assert hits == []
            assert (await client.get("/api/membersx")).status == 200
            assert hits == ["GET"]
            state.crewmate_prune_settled.set()
            assert (await roster).status == 200
            assert (await activity).status == 200
        assert hits == ["GET", "GET", "GET"]

    @pytest.mark.asyncio
    async def test_a_write_outside_api_is_held_too(self, monkeypatch):
        # ``POST /v1/chat/completions`` binds a session's agent like any
        # dashboard route; the gate keys on the method, never on the path.
        from aiohttp.test_utils import TestClient, TestServer

        from kiro_crew.dashboard import server as server_mod

        monkeypatch.setattr(server_mod, "_CREWMATE_PRUNE_GATE_TIMEOUT_S", 0.05)
        app, _state, hits = self._app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/v1/chat/completions")
            assert resp.status == 503
        assert hits == []

    @pytest.mark.asyncio
    async def test_a_pass_that_never_settles_answers_503(self, monkeypatch):
        from aiohttp.test_utils import TestClient, TestServer

        from kiro_crew.dashboard import server as server_mod

        monkeypatch.setattr(server_mod, "_CREWMATE_PRUNE_GATE_TIMEOUT_S", 0.05)
        app, _state, hits = self._app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/slots/s1/agent")
            assert resp.status == 503
            assert (await resp.json())["code"] == "prune_in_progress"
        assert hits == []

    @pytest.mark.asyncio
    async def test_once_settled_every_request_passes(self):
        from aiohttp.test_utils import TestClient, TestServer

        app, state, hits = self._app()
        state.crewmate_prune_settled.set()
        async with TestClient(TestServer(app)) as client:
            assert (await client.post("/api/chat/slots/s1/agent")).status == 200
        assert hits == ["POST"]


class TestAwaitSettled:
    """``await_crewmate_prune_settled``: the writer-side wait returns only once
    the pass has returned; on timeout it tells the pass to stop and keeps waiting."""

    @staticmethod
    def _state():
        import asyncio
        import threading
        from types import SimpleNamespace

        return SimpleNamespace(
            crewmate_prune_settled=asyncio.Event(),
            crewmate_prune_abandon=threading.Event(),
        )

    @pytest.mark.asyncio
    async def test_a_pass_that_settles_in_time_is_not_told_to_stop(self):
        import asyncio

        from kiro_crew.dashboard import server as server_mod

        state = self._state()
        waiter = asyncio.ensure_future(
            server_mod.await_crewmate_prune_settled(state, before="cron")
        )
        await asyncio.sleep(0.02)
        assert not waiter.done()
        state.crewmate_prune_settled.set()
        await waiter
        assert not state.crewmate_prune_abandon.is_set()

    @pytest.mark.asyncio
    async def test_a_pass_past_its_budget_is_told_to_stop_and_still_waited_for(self, monkeypatch):
        import asyncio

        from kiro_crew.dashboard import server as server_mod

        monkeypatch.setattr(server_mod, "_CREWMATE_PRUNE_GATE_TIMEOUT_S", 0.05)
        state = self._state()
        waiter = asyncio.ensure_future(
            server_mod.await_crewmate_prune_settled(state, before="cron")
        )
        await asyncio.sleep(0.2)
        assert state.crewmate_prune_abandon.is_set()
        assert not waiter.done()  # the writer does not start beside the pass
        state.crewmate_prune_settled.set()
        await asyncio.wait_for(waiter, timeout=1)

    @pytest.mark.asyncio
    async def test_deferred_transcript_removal_runs_only_after_the_pass_returns(self, monkeypatch):
        # The startup merge kept its copies while the pass was running; the
        # removing pass starts only once the event is set, with removal on and
        # the same claimed-slot set, and it is tracked like the pass itself.
        import asyncio

        from kiro_crew.dashboard import server as server_mod

        calls: list[dict] = []

        def _migrate(**kw):
            calls.append(kw)
            return 1

        monkeypatch.setattr(server_mod, "migrate_channel_transcripts", _migrate)
        state = self._state()
        state._background_tasks = set()
        claimed = frozenset({"chat-1"})
        server_mod._kick_deferred_transcript_removal(state, claimed)
        assert len(state._background_tasks) == 1
        await asyncio.sleep(0.05)
        assert calls == []  # nothing removed beside a pass that can still delete
        state.crewmate_prune_settled.set()
        for _ in range(50):
            if calls:
                break
            await asyncio.sleep(0.01)
        assert calls == [{"dashboard_slots": claimed, "remove": True}]
        for _ in range(50):
            if not state._background_tasks:
                break
            await asyncio.sleep(0.01)
        assert not state._background_tasks


class TestOneProcess:
    """The whole pass runs under a cross-process lock beside the marker, and the
    marker is created exclusively: two gateways on one data home cannot both
    prune, and the one that did not settles for the other's marker."""

    def test_a_pass_waits_for_the_holder_and_then_finds_its_marker(
        self, old_style_config, bindings_dir, log
    ):
        # "Another process": a second open of the lock file is a second open
        # file description, and the lock is per description, so a thread
        # stands in for it. It holds the lock, finishes its pass, writes the
        # marker, releases. This pass waits, then finds the marker and removes
        # nothing of its own -- the other pass already judged the rows.
        import threading

        from kiro_crew.platform_compat import file_lock, open_lock_file

        log.dm("radar")
        holding = threading.Event()
        release = threading.Event()

        def _other_gateway():
            with open_lock_file(mig.lock_path()) as fd, file_lock(fd, exclusive=True):
                holding.set()
                release.wait(5)
                mig._write_marker(mig.PruneReport(removed=["scout"], kept=["radar"]))

        other = threading.Thread(target=_other_gateway)
        other.start()
        assert holding.wait(5)
        threading.Timer(0.2, release.set).start()
        report = _run(log)
        other.join(5)
        assert report.skipped_marker is True
        assert report.lock_waits == 0
        assert report.removed == []
        # The other pass's record stands; this one wrote nothing over it.
        assert json.loads(mig.marker_path().read_text())["removed"] == ["scout"]
        # And the row is still here: the stand-in never deleted it, and this
        # pass, finding the marker, did not either.
        assert "scout" in KiroCrewConfig.load().agents

    def test_a_holder_that_outlasts_the_wait_is_waited_for_not_given_up_on(
        self, old_style_config, bindings_dir, log, monkeypatch
    ):
        # The contender's writers are held for exactly as long as its pass
        # runs, so a pass that RETURNED with the lock still held would let a
        # session bind a crewmate the holder has not judged yet, and the
        # holder would then delete a row in use. So the wait has no give-up:
        # each expired wait is one WARNING and one more try. Here the holder
        # outlasts several waits, then finishes WITHOUT a marker (it was the
        # kind of pass that judged nothing); the contender then takes the
        # lock and runs the pass itself.
        import threading

        from kiro_crew.platform_compat import file_lock, open_lock_file

        monkeypatch.setattr(mig, "PRUNE_LOCK_WAIT_S", 0.1)
        log.dm("radar")
        holding = threading.Event()
        release = threading.Event()

        def _other_gateway():
            with open_lock_file(mig.lock_path()) as fd, file_lock(fd, exclusive=True):
                holding.set()
                release.wait(5)

        other = threading.Thread(target=_other_gateway)
        other.start()
        assert holding.wait(5)
        threading.Timer(0.45, release.set).start()
        report = _run(log)
        other.join(5)
        # It waited through more than one budget rather than returning.
        assert report.lock_waits >= 2
        assert report.skipped_marker is False
        assert report.removed == ["scout"]
        assert report.kept == ["radar"]
        assert mig.marker_path().exists()

    def test_a_marker_that_appears_while_waiting_ends_the_wait(
        self, old_style_config, bindings_dir, log, monkeypatch
    ):
        # The marker is written last, under the lock: its presence means the
        # holder's deletes are done, so a waiting pass may settle on it even
        # before the holder lets go of the lock.
        from kiro_crew.platform_compat import file_lock, open_lock_file

        monkeypatch.setattr(mig, "PRUNE_LOCK_WAIT_S", 0.1)
        log.dm("radar")
        with open_lock_file(mig.lock_path()) as fd, file_lock(fd, exclusive=True):
            mig._write_marker(mig.PruneReport(removed=["scout"], kept=["radar"]))
            report = _run(log)
        assert report.skipped_marker is True
        assert report.lock_waits == 1
        assert report.removed == []
        assert "scout" in KiroCrewConfig.load().agents

    def test_the_marker_is_created_exclusively(self, bindings_dir):
        mig._write_marker(mig.PruneReport(kept=["radar"]))
        with pytest.raises(FileExistsError):
            mig._write_marker(mig.PruneReport(removed=["scout"]))
        marker = json.loads(mig.marker_path().read_text())
        assert marker["kept"] == ["radar"] and marker["removed"] == []

    def test_a_failure_inside_the_pass_is_not_read_as_contention(
        self, old_style_config, bindings_dir, log
    ):
        # Only the lock acquire may report "another process holds the pass";
        # an error from the pass proper is that error, so the gateway logs
        # the real cause instead of a phantom second gateway.
        with patch.object(mig, "_removal_candidates", side_effect=PermissionError("config")):
            with pytest.raises(PermissionError):
                _run(log)
        assert not mig.marker_path().exists()
        assert "scout" in KiroCrewConfig.load().agents

    def test_the_lock_is_released_after_the_pass(self, old_style_config, bindings_dir, log):
        from kiro_crew.platform_compat import file_lock, open_lock_file

        log.dm("radar")
        _run(log)
        # A second opener can take it at once: the pass did not leak its hold.
        with open_lock_file(mig.lock_path()) as fd:
            with file_lock(fd, exclusive=True, wait=False):
                pass

    def test_the_lock_is_released_when_the_pass_raises(self, old_style_config, bindings_dir, log):
        from kiro_crew.platform_compat import file_lock, open_lock_file

        with patch.object(mig, "_removal_candidates", side_effect=PermissionError("config")):
            with pytest.raises(PermissionError):
                _run(log)
        with open_lock_file(mig.lock_path()) as fd:
            with file_lock(fd, exclusive=True, wait=False):
                pass
