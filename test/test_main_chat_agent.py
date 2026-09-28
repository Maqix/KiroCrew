"""The ``kirocrew-main`` agent spec: the main chat's default agent plus session control.

The one-chat first run graduates into a MAIN CHAT from which the person hands long
work to a chat of its own. The session verbs live on ``kirocrew-dashboard``, an
``opt_in`` set the default agent never mounts, so the main chat runs on a spec of its
own and every other chat keeps the default agent's surface. Pinned here:

* the spec is the default agent AS IT STANDS ON DISK plus ``@kirocrew-dashboard`` --
  nothing the default carries is missing, nothing is narrowed;
* of that server, only ``session_create`` and ``session_read_message`` are
  auto-approved; ``session_send`` and ``session_stop`` stay mounted and gated;
* a governance ceiling that governs session control strips both grants, with the
  server still mounted;
* the spec is owned, installed eagerly on the rebuild path, covered by the spawn-path
  freshness gate, and read as the DEFAULT agent by the context and skill readers.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from kiro_crew import agent
from kiro_crew.agent_files import (
    AGENT_FILENAME,
    MAIN_CHAT_AGENT_FILENAME,
    MAIN_CHAT_AGENT_NAME,
    OWNED_KIRO_AGENT_FILES,
    PRIMARY_AGENT_NAMES,
    REQUIRED_KIRO_AGENT_FILES,
    is_primary_agent,
)
from kiro_crew.agent_sdk.drivers.acp import derived_agent_permissions
from kiro_crew.kiro_cli import SPEC_PERMISSIONS_MIN_VERSION

_DASHBOARD = "kirocrew-dashboard"
_GRANTED = (
    "@kirocrew-dashboard/session_create",
    "@kirocrew-dashboard/session_read_message",
)
_GATED = (
    "@kirocrew-dashboard/session_send",
    "@kirocrew-dashboard/session_stop",
)


@pytest.fixture(autouse=True)
def _accepting_kiro_cli(monkeypatch):
    """Pin an accepting kiro-cli, so the derived ``permissions`` block is written.

    CI has no binary, which reads as "unknown" and withholds the block; these tests
    assert it, so the answer must not depend on the host.
    """
    monkeypatch.setattr(
        "kiro_crew.kiro_cli.installed_kiro_cli_version", lambda: SPEC_PERMISSIONS_MIN_VERSION
    )


@pytest.fixture()
def agents_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(agent, "kiro_agents_dir_path", lambda: tmp_path)
    monkeypatch.setattr(agent, "_may_auto_approve", lambda ref: True)
    return tmp_path


def _used_default() -> dict[str, Any]:
    """The template plus what a used install adds to ``kirocrew.json`` and nowhere else.

    A server a first-run ``connect`` card wrote, an app's server with a whole-server
    grant, and a model pick: none of it reaches ``build_agent_config``, which is why
    the main chat mirrors the FILE.
    """
    spec = agent.build_agent_config()
    spec["model"] = "a-picked-model"
    spec["mcpServers"]["github"] = {"url": "https://mcp.example.invalid/github"}
    spec["mcpServers"]["notes:server"] = {"command": "notes-mcp", "args": []}
    spec["tools"] = [*spec["tools"], "@github", "@notes:server"]
    spec["allowedTools"] = [*spec["allowedTools"], "@notes:server"]
    return spec


def _install(agents_dir: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if default is not None:
        (agents_dir / AGENT_FILENAME).write_text(json.dumps(default), encoding="utf-8")
    agent._install_main_chat_agent()
    return json.loads((agents_dir / MAIN_CHAT_AGENT_FILENAME).read_text(encoding="utf-8"))


def _dashboard_grants(spec: dict[str, Any]) -> list[str]:
    return [ref for ref in spec["allowedTools"] if ref.startswith(f"@{_DASHBOARD}")]


# ── the default agent, plus one server ────────────────────────────────────


def test_it_carries_every_tool_server_and_grant_the_default_carries(agents_dir):
    """Superset of the default spec ON DISK: a server the person connected in the first
    run is in the main chat too, and the default's own grants are all still there."""
    default = _used_default()
    main = _install(agents_dir, default)

    assert set(default["tools"]) <= set(main["tools"])
    assert set(default["mcpServers"]) <= set(main["mcpServers"])
    for name, entry in default["mcpServers"].items():
        assert main["mcpServers"][name] == entry, name
    assert set(default["allowedTools"]) <= set(main["allowedTools"])
    assert main["model"] == "a-picked-model"
    assert main.get("excludedTools") == default.get("excludedTools")


def test_it_adds_exactly_the_dashboard_server(agents_dir):
    default = _used_default()
    main = _install(agents_dir, default)

    assert set(main["tools"]) - set(default["tools"]) == {f"@{_DASHBOARD}"}
    assert set(main["mcpServers"]) - set(default["mcpServers"]) == {_DASHBOARD}
    assert set(main["allowedTools"]) - set(default["allowedTools"]) == set(_GRANTED)


def test_the_prompt_hooks_and_resources_are_the_default_agents(agents_dir):
    """Not a conductor: the managed prompt stub (so the operating contract is injected
    once, resolved), the bundled security hooks, and the template's resources."""
    template = agent.build_agent_config()
    main = _install(agents_dir, _used_default())

    assert main["name"] == MAIN_CHAT_AGENT_NAME == "kirocrew-main"
    assert agent.is_managed_prompt(main["prompt"])
    assert main["hooks"] == template["hooks"]
    assert main["resources"] == template["resources"]
    assert main["includeMcpJson"] is False


def test_the_dashboard_entry_is_hand_built_with_no_auto_approve(agents_dir, monkeypatch):
    """Built the conductor's way -- the managed invocation plus the data-home pin --
    and never with an ``autoApprove`` key, which would skip the PreToolUse gate."""
    monkeypatch.setattr(agent, "_kirocrew_mcp_invocation", lambda sub: ("/bin/kirocrew", [sub]))
    monkeypatch.setattr(agent, "_managed_mcp_env", lambda: {"KIROCREW_HOME": "/elsewhere"})
    main = _install(agents_dir, _used_default())

    entry = main["mcpServers"][_DASHBOARD]
    assert entry["command"] == "/bin/kirocrew"
    assert entry["args"] == ["mcp-dashboard"]
    assert entry["env"] == {"KIROCREW_HOME": "/elsewhere"}
    assert "autoApprove" not in entry


def test_it_falls_back_to_the_template_with_no_default_on_disk(agents_dir):
    main = _install(agents_dir)
    template = agent.build_agent_config()
    assert set(template["tools"]) <= set(main["tools"])
    assert f"@{_DASHBOARD}" in main["tools"]
    assert _DASHBOARD in main["mcpServers"]


# ── create and read auto-approved; send and stop gated ────────────────────


def test_only_create_and_read_are_auto_approved_on_the_dashboard_server(agents_dir):
    main = _install(agents_dir, _used_default())

    assert sorted(_dashboard_grants(main)) == sorted(_GRANTED)
    for ref in _GATED:
        assert ref not in main["allowedTools"], ref
    # Still MOUNTED whole: the gated verbs are callable, through the approval gate.
    assert f"@{_DASHBOARD}" in main["tools"]


def test_the_kas_policy_carries_the_two_verbs_and_nothing_else_of_the_server(agents_dir):
    main = _install(agents_dir, _used_default())

    assert main["permissions"] == derived_agent_permissions(
        main["allowedTools"], MAIN_CHAT_AGENT_FILENAME
    )
    matches = [
        m
        for rule in main["permissions"]["rules"]
        for m in rule.get("match", [])
        if m.startswith(f"{_DASHBOARD}/") or m == f"{_DASHBOARD}/*"
    ]
    assert sorted(matches) == sorted(ref[1:] for ref in _GRANTED)


def test_a_mirrored_dashboard_grant_does_not_widen_the_main_chat(agents_dir):
    """A default that mounts the server with a WHOLE-server grant hands every one of
    its chats the verbs already; the main chat's own additions stay create and read,
    and the mirrored entry's ``autoApprove`` does not ride across."""
    default = _used_default()
    default["tools"].append(f"@{_DASHBOARD}")
    default["allowedTools"].extend([f"@{_DASHBOARD}", f"@{_DASHBOARD}/session_send"])
    default["mcpServers"][_DASHBOARD] = {
        "command": "stale",
        "args": [],
        "autoApprove": ["session_stop"],
        "disabledTools": ["chat_session_pin"],
    }
    main = _install(agents_dir, default)

    assert sorted(_dashboard_grants(main)) == sorted(_GRANTED)
    entry = main["mcpServers"][_DASHBOARD]
    assert "autoApprove" not in entry
    assert entry["command"] != "stale"
    # A restriction the operator put on the server survives the replacement.
    assert entry["disabledTools"] == ["chat_session_pin"]


# ── the governance ceiling ────────────────────────────────────────────────


def test_a_ceiling_governing_session_control_strips_both_grants(agents_dir, monkeypatch):
    """A real ceiling, not a stub: an ``mcp`` rule naming the dashboard server. The
    predicate is whole-server, so both grants go and both verbs prompt, while the
    rest of the default agent's grants are untouched."""
    from kiro_crew.platform import governance as gov

    ceiling = gov.GovernanceCeiling(
        version=1,
        boot=gov.BootControls(),
        controls={
            "mcp": gov.ScopedRuleset(
                mode="deny", allow=(), deny=("@kirocrew-dashboard/session_create",)
            )
        },
    )
    monkeypatch.setattr(agent, "_may_auto_approve", lambda ref: gov.may_skip_gate(ref, ceiling))
    main = _install(agents_dir, _used_default())

    assert _dashboard_grants(main) == []
    assert f"@{_DASHBOARD}" in main["tools"], "the ceiling removes auto-approve, not the tool"
    assert _DASHBOARD in main["mcpServers"]
    assert "@kirocrew-core" in main["allowedTools"], "an unrelated grant is not collateral"
    assert all(
        not m.startswith(f"{_DASHBOARD}/")
        for rule in main["permissions"]["rules"]
        for m in rule.get("match", [])
    )


def test_a_fully_governed_host_leaves_nothing_auto_approved(agents_dir, monkeypatch):
    monkeypatch.setattr(agent, "_may_auto_approve", lambda ref: False)
    main = _install(agents_dir, _used_default())
    assert main["allowedTools"] == []
    assert main["permissions"] == {"rules": []}
    assert f"@{_DASHBOARD}" in main["tools"]


def test_a_withheld_grant_is_sel_audited(agents_dir, monkeypatch):
    calls: list[dict] = []

    class _Recorder:
        def log_api_access(self, **kw):
            calls.append(kw)

    monkeypatch.setattr(agent, "sel", lambda: _Recorder())
    monkeypatch.setattr(agent, "_may_auto_approve", lambda ref: not ref.startswith("@kirocrew-d"))
    _install(agents_dir, _used_default())

    withheld = [
        c
        for c in calls
        if c.get("operation") == "mcp_auto_approve_withheld"
        and c.get("source") == "_install_main_chat_agent"
    ]
    assert len(withheld) == 1
    for ref in _GRANTED:
        assert ref in withheld[0]["resources"]


def test_the_final_server_map_crosses_the_auto_approve_sanitizer(agents_dir, monkeypatch):
    """The second gate-skipping channel: ``autoApprove`` on a mirrored entry, which the
    grant filter cannot see. The whole FINAL map -- mirror plus the dashboard entry --
    goes through the same sanitizer the default spec's rebuild uses."""
    default = _used_default()
    default["mcpServers"]["github"]["autoApprove"] = ["delete_repo"]
    seen: list[set[str]] = []

    def _strip_all(servers):
        seen.append(set(servers))
        return {
            name: {k: v for k, v in entry.items() if k != "autoApprove"}
            for name, entry in servers.items()
        }

    monkeypatch.setattr(agent, "_strip_ungoverned_auto_approve", _strip_all)
    main = _install(agents_dir, default)
    assert "autoApprove" not in main["mcpServers"]["github"]
    assert seen and {"github", _DASHBOARD} <= seen[-1]


# ── ownership, eager install, freshness ───────────────────────────────────


def test_the_filename_is_owned_and_optional():
    assert MAIN_CHAT_AGENT_FILENAME == "kirocrew-main.json"
    assert MAIN_CHAT_AGENT_FILENAME in OWNED_KIRO_AGENT_FILES
    # A failed install costs the main chat its session tools, not every turn.
    assert MAIN_CHAT_AGENT_FILENAME not in REQUIRED_KIRO_AGENT_FILES


def test_it_is_installed_eagerly_after_the_default_spec_is_written():
    """Eager because a slot naming a spec the boot snapshot does not know runs the
    default agent instead; after the default's write because it mirrors that file."""
    src = inspect.getsource(agent.rebuild_agent_config)
    assert "_install_main_chat_agent()" in src
    assert src.index('logger.info("Installed agent config: %s", path)') < src.index(
        "_install_main_chat_agent()"
    )


def test_the_spawn_gate_re_derives_a_stale_main_chat_mirror(agents_dir):
    """A server revoked on the default out of band (an app writer that does not rebuild)
    must not stay mounted and auto-approved on the main chat."""
    default = _used_default()
    _install(agents_dir, default)

    scrubbed = json.loads((agents_dir / AGENT_FILENAME).read_text(encoding="utf-8"))
    del scrubbed["mcpServers"]["notes:server"]
    scrubbed["tools"].remove("@notes:server")
    scrubbed["allowedTools"].remove("@notes:server")
    (agents_dir / AGENT_FILENAME).write_text(json.dumps(scrubbed), encoding="utf-8")

    snapshot = agent.require_fresh_derived_spec(MAIN_CHAT_AGENT_NAME, None)
    assert snapshot is not None
    main = json.loads((agents_dir / MAIN_CHAT_AGENT_FILENAME).read_text(encoding="utf-8"))
    assert "notes:server" not in main["mcpServers"]
    assert "@notes:server" not in main["allowedTools"]
    assert snapshot.spec == main
    assert sorted(_dashboard_grants(main)) == sorted(_GRANTED)


def test_a_fresh_main_chat_mirror_is_not_rewritten(agents_dir, monkeypatch):
    _install(agents_dir, _used_default())
    called: list[str] = []
    monkeypatch.setattr(
        agent, "rederive_main_chat_agent", lambda reason: called.append(reason) or True
    )
    assert agent.require_fresh_derived_spec(MAIN_CHAT_AGENT_NAME, None) is not None
    assert called == []


def test_a_project_shadow_of_the_main_chat_spec_refuses_the_spawn(agents_dir, tmp_path):
    """kiro-cli resolves a checkout's own spec first, and no derivation touches it."""
    _install(agents_dir, _used_default())
    checkout = tmp_path / "checkout"
    (checkout / ".kiro" / "agents").mkdir(parents=True)
    (checkout / ".kiro" / "agents" / MAIN_CHAT_AGENT_FILENAME).write_text(
        json.dumps({"name": MAIN_CHAT_AGENT_NAME, "allowedTools": ["*"]}), encoding="utf-8"
    )
    with pytest.raises(agent.DerivedSpecStale) as excinfo:
        agent.require_fresh_derived_spec(MAIN_CHAT_AGENT_NAME, checkout)
    assert "project checkout declares its own kirocrew-main" in str(excinfo.value)


# ── read as the default agent, not a custom one ───────────────────────────


def test_it_is_a_primary_agent_and_nothing_else_is_added():
    assert PRIMARY_AGENT_NAMES == frozenset({"kirocrew", "kirocrew-main"})
    assert is_primary_agent("kirocrew-main")
    assert not is_primary_agent("kirocrew-worker")
    assert not is_primary_agent(None)


def test_the_main_chat_gets_the_whole_skill_catalog_like_the_default(agents_dir):
    """Row 1 of the context-management skills table, not row 3: an unmapped custom
    agent sees no skills, and the main chat is where the person runs everything."""
    from kiro_crew import context as ctx_mod

    _install(agents_dir, _used_default())
    for is_cc in (False, True):
        assert ctx_mod._skills_injection_plan(MAIN_CHAT_AGENT_NAME, is_cc=is_cc) == (True, [])


def test_the_main_chat_always_gets_crew_context():
    from kiro_crew import context as ctx_mod

    assert ctx_mod._agent_includes_crew_context(MAIN_CHAT_AGENT_NAME) is True
