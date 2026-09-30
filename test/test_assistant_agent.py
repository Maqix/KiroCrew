"""The managed ``kirocrew-assistant`` template.

Pins the narrowed default toolset plus the assistant's guide mount and two
ceiling-filtered read grants, the operator's model rather than a literal, a skill
mapping that reaches the packaged skills, and a
prompt that teaches the assistant role without naming tools this build does not
ship. Every test writes to a private agents dir under the isolated data home;
nothing here touches a live ``~/.kiro/agents``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kiro_crew import agent, agent_state
from kiro_crew.agent_files import AGENT_FILENAME, ASSISTANT_AGENT_FILENAME, OWNED_KIRO_AGENT_FILES
from kiro_crew.kiro_cli import SPEC_PERMISSIONS_MIN_VERSION

GUIDE_SERVER = "kirocrew-guide"
GUIDE_REF = f"@{GUIDE_SERVER}"
GUIDE_READ_REFS = {
    f"{GUIDE_REF}/guide_list_actions",
    f"{GUIDE_REF}/guide_status",
}


@pytest.fixture()
def agents_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "agents"
    directory.mkdir()
    monkeypatch.setattr(agent, "kiro_agents_dir_path", lambda: directory)
    monkeypatch.setattr(agent, "KIRO_AGENTS_DIR", directory)
    monkeypatch.setattr(
        "kiro_crew.kiro_cli.installed_kiro_cli_version", lambda: SPEC_PERMISSIONS_MIN_VERSION
    )
    return directory


def _install(agents_dir: Path) -> dict[str, Any]:
    agent._install_assistant_agent()
    return json.loads((agents_dir / ASSISTANT_AGENT_FILENAME).read_text(encoding="utf-8"))


def _refs(value: object) -> list[str]:
    return [ref for ref in value if isinstance(ref, str)] if isinstance(value, list) else []


def test_the_assistant_is_a_managed_file() -> None:
    assert ASSISTANT_AGENT_FILENAME == "kirocrew-assistant.json"
    assert ASSISTANT_AGENT_FILENAME in OWNED_KIRO_AGENT_FILES


def test_the_spec_is_the_template_or_narrower(agents_dir: Path) -> None:
    spec = _install(agents_dir)
    template = agent.build_agent_config()
    assert spec["name"] == "kirocrew-assistant"
    # The ONE explicit widening is the gated guide mount; everything else is the
    # template or narrower.
    tools = set(_refs(spec["tools"])) - {GUIDE_REF}
    servers = set(spec["mcpServers"]) - {GUIDE_SERVER}
    assert tools <= set(_refs(template["tools"]))
    assert set(_refs(spec["allowedTools"])) - GUIDE_READ_REFS <= set(
        _refs(template["allowedTools"])
    )
    assert "*" not in spec["tools"] and "*" not in spec["allowedTools"]
    assert servers <= set(template["mcpServers"])
    for name in servers:
        entry = spec["mcpServers"][name]
        theirs = template["mcpServers"][name].get("autoApprove") or []
        assert set(entry.get("autoApprove") or []) <= set(theirs), name
    # Governance travels unchanged: bundled hooks and the subagent allowlist.
    assert spec["hooks"] == template["hooks"]
    assert spec.get("toolsSettings") == template.get("toolsSettings")
    assert spec["includeMcpJson"] is False
    # The KAS block is derived from the final grant list, never widened.
    from kiro_crew.agent_sdk.drivers.acp import derived_agent_permissions

    assert spec["permissions"] == derived_agent_permissions(
        spec["allowedTools"], ASSISTANT_AGENT_FILENAME
    )


def test_a_narrowed_default_narrows_the_assistant(agents_dir: Path) -> None:
    template = agent.build_agent_config()
    granted = _refs(template["allowedTools"])
    assert len(granted) >= 2, "the template grants something to narrow"
    keep = granted[-1]
    (agents_dir / AGENT_FILENAME).write_text(
        json.dumps(
            {
                "name": "kirocrew",
                "tools": ["fs_read", "grep", "@kirocrew-core", "@user-only"],
                "allowedTools": [keep, "@user-only"],
            }
        ),
        encoding="utf-8",
    )
    spec = _install(agents_dir)
    # Narrowing runs BEFORE the explicit guide grant, so a default that never
    # names the opt-in set cannot narrow it back out.
    assert spec["tools"] == ["fs_read", "grep", "@kirocrew-core", GUIDE_REF]
    assert set(spec["allowedTools"]) == {keep} | GUIDE_READ_REFS
    # A server no remaining ref names is not mounted.
    assert set(spec["mcpServers"]) == {"kirocrew-core", GUIDE_SERVER}
    # A default-only entry never arrives: the intersection only removes.
    assert "@user-only" not in spec["tools"] + spec["allowedTools"]


def test_a_wildcard_default_imposes_no_narrowing(agents_dir: Path) -> None:
    (agents_dir / AGENT_FILENAME).write_text(
        json.dumps({"name": "kirocrew", "tools": ["*"], "allowedTools": ["*"]}), encoding="utf-8"
    )
    spec = _install(agents_dir)
    template = agent.build_agent_config()
    assert spec["tools"] == template["tools"] + [GUIDE_REF]
    assert set(spec["allowedTools"]) == set(template["allowedTools"]) | GUIDE_READ_REFS


def test_only_guide_reads_are_auto_approved_on_the_assistant(agents_dir: Path) -> None:
    spec = _install(agents_dir)
    assert GUIDE_REF in spec["tools"]
    entry = spec["mcpServers"][GUIDE_SERVER]
    assert entry["args"][-1] == "mcp-guide"
    assert "autoApprove" not in entry
    guide_grants = {
        ref
        for ref in _refs(spec["allowedTools"])
        if ref == GUIDE_REF or ref.startswith(f"{GUIDE_REF}/")
    }
    assert guide_grants == GUIDE_READ_REFS
    matches = {
        match
        for rule in spec["permissions"]["rules"]
        if rule["capability"] == "mcp" and rule["effect"] == "allow"
        for match in rule.get("match", [])
        if match.startswith(f"{GUIDE_SERVER}/")
    }
    assert matches == {ref.removeprefix("@") for ref in GUIDE_READ_REFS}
    assert "autoApprove" not in agent._MANAGED_MCP_SERVERS[GUIDE_SERVER]
    template = agent.build_agent_config()
    assert GUIDE_SERVER not in template["mcpServers"]
    assert GUIDE_REF not in _refs(template["tools"])


@pytest.mark.parametrize(
    "denied",
    [GUIDE_READ_REFS, {f"{GUIDE_REF}/guide_status"}],
)
def test_guide_read_grants_respect_the_ceiling(
    agents_dir: Path, monkeypatch: pytest.MonkeyPatch, denied: set[str]
) -> None:
    monkeypatch.setattr(agent, "_may_auto_approve", lambda ref: ref not in denied)
    spec = _install(agents_dir)
    assert GUIDE_REF in spec["tools"]
    assert GUIDE_SERVER in spec["mcpServers"]
    assert GUIDE_READ_REFS.intersection(spec["allowedTools"]) == GUIDE_READ_REFS - denied
    matches = {
        match
        for rule in spec["permissions"]["rules"]
        if rule["capability"] == "mcp" and rule["effect"] == "allow"
        for match in rule.get("match", [])
    }
    assert not {ref.removeprefix("@") for ref in denied}.intersection(matches)


def test_the_ceiling_withholds_on_the_assistant_too(
    agents_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent, "_may_auto_approve", lambda ref: ref != "@kirocrew-core")
    spec = _install(agents_dir)
    assert "@kirocrew-core" not in spec["allowedTools"]
    assert "@kirocrew-core" in spec["tools"]


def test_the_model_follows_the_operator_and_never_a_literal(
    agents_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kiro_crew.config.loader import KiroCrewConfig

    assert _install(agents_dir)["model"] == "auto"
    monkeypatch.setattr(
        KiroCrewConfig, "load", lambda *a, **k: SimpleNamespace(agent=SimpleNamespace(model="op"))
    )
    assert _install(agents_dir)["model"] == "op"

    def broken(*_a: object, **_k: object) -> None:
        raise OSError("unreadable")

    monkeypatch.setattr(KiroCrewConfig, "load", broken)
    assert _install(agents_dir)["model"] == "auto"


def test_an_explicit_model_pick_survives_a_rebuild(
    agents_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = agents_dir / ASSISTANT_AGENT_FILENAME
    spec = _install(agents_dir)
    spec["model"] = "picked"
    path.write_text(json.dumps(spec), encoding="utf-8")
    assert _install(agents_dir)["model"] == "auto"  # no pin recorded: propagation
    spec["model"] = "picked"
    path.write_text(json.dumps(spec), encoding="utf-8")
    agent_state.set_model_managed("kirocrew-assistant", False)
    assert _install(agents_dir)["model"] == "picked"


def test_a_foreign_file_at_the_path_is_left_alone(agents_dir: Path) -> None:
    foreign = {"name": "kirocrew-assistant", "prompt": "my own persona", "tools": ["*"]}
    path = agents_dir / ASSISTANT_AGENT_FILENAME
    path.write_text(json.dumps(foreign), encoding="utf-8")
    agent._install_assistant_agent()
    assert json.loads(path.read_text(encoding="utf-8")) == foreign


def test_reinstall_is_stable(agents_dir: Path) -> None:
    first = _install(agents_dir)
    assert _install(agents_dir) == first


def test_the_skill_mapping_reaches_the_packaged_skills(agents_dir: Path) -> None:
    from kiro_crew.agent_discovery import agent_skill_globs
    from kiro_crew.config import config_dir

    spec = _install(agents_dir)
    skills = [r for r in spec["resources"] if r.startswith("skill://")]
    home_glob = f"{(config_dir() / 'skills').as_posix()}/*/SKILL.md"
    assert f"skill://{home_glob}" in skills
    # The template's steering glob is kept, not replaced.
    assert set(agent.build_agent_config().get("resources") or []) <= set(spec["resources"])
    globs = agent_skill_globs("kirocrew-assistant", agents_dir=agents_dir)
    assert globs, "a custom agent without a mapping receives no skill directory"
    import fnmatch

    builtin = (config_dir() / "skills" / "kirocrew-commands" / "SKILL.md").as_posix()
    assert any(fnmatch.fnmatch(builtin, g.replace("\\", "/")) for g in globs)


def test_the_prompt_teaches_the_assistant_role(agents_dir: Path) -> None:
    prompt = _install(agents_dir)["prompt"]
    assert prompt.startswith(agent._ASSISTANT_PROMPT_HEADER)
    assert "{docs_index}" not in prompt
    docs_index = Path(agent.__file__).resolve().parent / "docs" / "README.md"
    assert docs_index.as_posix() in prompt and docs_index.is_file()
    assert "/members?create=1&name=<URL-encoded name>&goal=<URL-encoded goal>" in prompt
    assert "does not create a crewmate" in prompt
    for tool in ("memory_recall", "search_chat_history", "get_chat_session", "list_sessions"):
        assert f"`{tool}`" in prompt
    assert "kirocrew-commands" in prompt
    # Calibrates to the onboarding profile the session context already injects,
    # without letting it override the request or gate ordinary help.
    assert "`[USER PROFILE]`" in prompt
    assert "Technical comfort is separate from job role" in prompt
    assert "current explicit request always wins" in prompt
    assert "do not guess the user's profession" in prompt
    # Guides point; the user makes the change.
    assert "`guide_start`" in prompt and "the user makes the change" in prompt
    # Not the managed stub: it is this template's own persona.
    assert not agent.is_managed_prompt(prompt)


def test_every_tool_the_prompt_names_is_shipped(agents_dir: Path) -> None:
    """The prompt must not imply a tool (or an operation server) this build lacks."""
    spec = _install(agents_dir)
    titles = json.loads(
        (Path(agent.__file__).resolve().parent / "data" / "mcp_tool_titles.json").read_text(
            encoding="utf-8"
        )
    )
    shipped = {name for tools in titles.values() for name in tools} | set(_refs(spec["tools"]))
    from kiro_crew import mcp_guide

    shipped |= {tool["name"] for tool in mcp_guide._list_tools()}
    named = set(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", spec["prompt"]))
    assert named, "the prompt names its tools in backticks"
    assert named <= shipped, named - shipped
    # No MCP server reference beyond what the spec mounts.
    servers = set(re.findall(r"@([a-z][a-z0-9-]+)", spec["prompt"]))
    assert servers <= set(spec["mcpServers"])


def test_rebuild_installs_and_adopts_default_without_changing_chat_template(
    agents_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from kiro_crew.config import config_path

    bindir = tmp_path / "bin"
    bindir.mkdir()
    launcher = bindir / "kirocrew"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    monkeypatch.setattr(agent, "_KIROCREW_BIN", str(launcher))
    monkeypatch.setattr(agent, "_KIRO_MCP_JSON", tmp_path / "kiro-global-mcp.json")
    monkeypatch.setattr(agent, "_DEFAULT_KIRO_HOOKS_DIR", tmp_path / "hooks")
    monkeypatch.setattr(
        "kiro_crew.apps.bridges._mcp_json_path", lambda: agents_dir / AGENT_FILENAME
    )
    agent.rebuild_agent_config()
    assert (agents_dir / ASSISTANT_AGENT_FILENAME).is_file()
    default = json.loads((agents_dir / AGENT_FILENAME).read_text(encoding="utf-8"))
    assert default["name"] == "kirocrew"
    after = json.loads(config_path().read_text(encoding="utf-8"))
    assert after["agents"]["default"]["kiro_agent"] == "kirocrew-assistant"
    assert after.get("agent", {}).get("default_agent", "kirocrew") == "kirocrew"


def test_default_adoption_preserves_all_other_fields_and_honors_later_opt_out(agents_dir):
    from kiro_crew.config.loader import config_path, update_config_locked

    original = {
        "agents": {
            "default": {
                "kiro_agent": "kirocrew",
                "display_name": "Mochi",
                "workspace": "work",
                "memory_store": "default",
                "description": "My custom role",
            }
        },
        "agent": {"default_agent": "kirocrew"},
        "default_agent": "default",
        "dashboard": {"user_role": "designer"},
    }
    update_config_locked(mutate=lambda _: original)
    _install(agents_dir)
    agent._bind_default_assistant_once()
    saved = json.loads(config_path().read_text(encoding="utf-8"))
    expected = original["agents"]["default"] | {"kiro_agent": "kirocrew-assistant"}
    assert saved["agents"]["default"] == expected
    assert saved["agent"] == original["agent"]
    assert saved["dashboard"] == original["dashboard"]
    saved["agents"]["default"]["kiro_agent"] = "kirocrew"
    update_config_locked(mutate=lambda _: saved)
    before = config_path().read_bytes()
    agent._bind_default_assistant_once()
    assert config_path().read_bytes() == before


@pytest.mark.parametrize(
    "member",
    [
        {"kiro_agent": "custom-template"},
        {"kiro_agent": "kirocrew", "member_id": "v2-identity"},
        {"kiro_agent": "kirocrew", "memory_store": "private"},
    ],
)
def test_default_adoption_never_rebinds_custom_members(agents_dir, member):
    from kiro_crew.config.loader import config_path, update_config_locked

    update_config_locked(mutate=lambda _: {"agents": {"default": member}})
    _install(agents_dir)
    before = config_path().read_bytes()
    agent._bind_default_assistant_once()
    assert config_path().read_bytes() == before


def test_overlay_binding_is_not_replaced(agents_dir):
    from kiro_crew.config.loader import config_local_path, config_path, update_config_locked

    update_config_locked(mutate=lambda _: {"agents": {"default": {"kiro_agent": "kirocrew"}}})
    update_config_locked(
        config_local_path(),
        mutate=lambda _: {"agents": {"default": {"kiro_agent": "my-template"}}},
        stamp_meta=False,
    )
    _install(agents_dir)
    before = config_path().read_bytes()
    agent._bind_default_assistant_once()
    assert config_path().read_bytes() == before


def test_unreadable_assistant_template_is_preserved(agents_dir):
    target = agents_dir / ASSISTANT_AGENT_FILENAME
    target.write_text("{broken", encoding="utf-8")
    assert agent._install_assistant_agent() is False
    assert target.read_text(encoding="utf-8") == "{broken"


def test_unseeded_overlay_chat_template_is_not_replaced(agents_dir):
    from kiro_crew.config.loader import config_local_path, config_path, update_config_locked

    update_config_locked(mutate=lambda _: {"dashboard": {"user_role": "designer"}})
    update_config_locked(
        config_local_path(),
        mutate=lambda _: {"agent": {"default_agent": "custom-template"}},
        stamp_meta=False,
    )
    before = config_path().read_bytes()
    agent._bind_default_assistant_once()
    assert config_path().read_bytes() == before
