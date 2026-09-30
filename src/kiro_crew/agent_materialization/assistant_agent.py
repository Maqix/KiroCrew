"""The ``kirocrew-assistant`` template and the reserved default member's one-time adoption.

The assistant is derived from the default template, so it inherits the governance
ceiling, then narrowed to what the on-disk default still mounts and grants, and given
exactly one extra set: the ``kirocrew-guide`` server with its two read tools
auto-approved. Its prompt and grant tuple are spec text and stay in
:mod:`kiro_crew.agent`. A file at the template path this installer did not write is
never replaced. :func:`_bind_default_assistant_once` points the untouched reserved
``default`` member at the template once; an operator's later choice is kept.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kiro_crew import agent as agent_mod
from kiro_crew import agent_state
from kiro_crew.agent_files import ASSISTANT_AGENT_FILENAME as _ASSISTANT_AGENT_FILENAME
from kiro_crew.agent_materialization import auto_approve, managed_mcp, worker_agent


def _assistant_skill_resources() -> list[str]:
    """``skill://`` mappings that give the assistant the default skill catalog.

    A custom agent with no ``skill://`` mapping gets NO skill directory at all
    (``context._skills_injection_plan``), so without these the assistant could not
    reach the packaged Kiro Crew skills it is told to load. The three roots are the
    ones the default catalog reads from on a public install: the data home's
    ``skills/`` (the packaged builtins land there, alongside the user's own), the
    open-standard ``~/.kiro/skills/``, and the session project's
    ``.kiro/skills/``. The data home is written as an absolute path so the mapping
    follows a ``KIROCREW_HOME`` override. Skills are listed in a bounded directory
    and their bodies load on demand, so mapping whole roots injects no manual.
    """
    home_skills = (agent_mod.config_dir() / "skills").as_posix()
    return [
        f"skill://{home_skills}/*/SKILL.md",
        "skill://~/.kiro/skills/*/SKILL.md",
        "skill://.kiro/skills/*/SKILL.md",
    ]


def _assistant_model_is_user_pinned() -> bool:
    """True when the dashboard recorded an explicit model pick for the assistant.

    Same three-state reading as the worker's model pin: a recorded ``False`` for
    ``model_managed`` is a pin, no entry is propagation, and an unreadable sidecar
    fails CLOSED (keep the file's model) because the pin's value is recorded nowhere
    else.
    """
    try:
        return agent_state.get_model_managed("kirocrew-assistant", strict=True) is False
    except (OSError, ValueError):
        agent_mod.logger.warning(
            "Agent state sidecar unreadable; keeping the assistant spec's own model",
            exc_info=True,
        )
        return True


def _narrow_to_installed_default(config: dict[str, Any]) -> None:
    """Keep only the ``tools`` / ``allowedTools`` the on-disk default also carries.

    The base toolset inherits the user's normal agent restrictions. The default
    spec's ``tools`` and ``allowedTools`` are user-owned, so a user who removed a
    tool or a grant there would otherwise find it back on the assistant. An exact
    string intersection only ever removes entries; a default whose value is the
    ``"*"`` wildcard, or absent/malformed, imposes no narrowing for that key. A
    server whose every ``tools`` reference is gone is dropped from
    ``mcpServers`` so kiro-cli does not spawn a server nothing can call.
    """
    installed = worker_agent._installed_default_spec()
    if installed is None:
        return
    for key in ("tools", "allowedTools"):
        theirs = installed.get(key)
        if not isinstance(theirs, list) or "*" in theirs:
            continue
        allowed = {ref for ref in theirs if isinstance(ref, str)}
        ours = config.get(key)
        if isinstance(ours, list):
            config[key] = [ref for ref in ours if isinstance(ref, str) and ref in allowed]
    tools = [ref for ref in (config.get("tools") or []) if isinstance(ref, str)]
    servers = config.get("mcpServers")
    if isinstance(servers, dict):
        config["mcpServers"] = {
            name: entry
            for name, entry in servers.items()
            if any(ref == f"@{name}" or ref.startswith(f"@{name}/") for ref in tools)
        }


def _grant_assistant_guide_set(config: dict[str, Any]) -> None:
    """Mount the guide set and grant ceiling-filtered reads on the assistant.

    Guide discovery and status use exact tool grants
    (:data:`kiro_crew.agent._ASSISTANT_GUIDE_READ_GRANTS`). Starting and cancelling
    a guide require approval; browser acceptance and save controls remain separate.
    """
    server = agent_mod._ASSISTANT_GUIDE_SERVER
    tools = [ref for ref in (config.get("tools") or []) if isinstance(ref, str)]
    ref = f"@{server}"
    if ref not in tools:
        tools.append(ref)
    config["tools"] = tools
    mcp = dict(config.get("mcpServers") or {})
    mcp[server] = managed_mcp._managed_opt_in_entry("mcp-guide")
    config["mcpServers"] = mcp
    allowed = list(config.get("allowedTools") or [])
    allowed.extend(g for g in agent_mod._ASSISTANT_GUIDE_READ_GRANTS if g not in allowed)
    config["allowedTools"] = allowed
    auto_approve._apply_allowed_tools_ceiling(config, source="assistant-guide")


def _install_assistant_agent() -> bool:
    """Generate and install the ``kirocrew-assistant`` agent config.

    Derived from :func:`kiro_crew.agent.build_agent_config`: managed servers, bundled
    hooks, ``toolsSettings`` and ceiling-filtered grants. The default's mounted and
    auto-approved lists narrow the base through :func:`_narrow_to_installed_default`.
    The assistant additionally mounts ``kirocrew-guide`` and grants only its two
    read tools, subject to the same governance ceiling. Server-level auto-approval
    is filtered by the governance strip. The KAS ``permissions`` block is derived
    from the final list through the shared version gate.

    The model follows the operator's configured chat model (``auto`` when unset),
    and an explicit per-agent pick recorded by the dashboard is kept across
    rebuilds, the same rule the worker applies. The prompt is the assistant's own
    (:data:`kiro_crew.agent._ASSISTANT_SYSTEM_PROMPT`) and the skill catalog is
    mapped explicitly (:func:`_assistant_skill_resources`), because a custom agent
    without a mapping receives no skill directory.

    Nothing binds this template: installing it changes no crew binding and not
    the ordinary chat default. A file at this path that this installer did not
    write (wrong ``name``, or a prompt without the header mark) is left alone and
    ``False`` is returned.
    """
    from kiro_crew.config.loader import KiroCrewConfig

    agents_dir = agent_mod.kiro_agents_dir_path()
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / _ASSISTANT_AGENT_FILENAME
    with agent_mod.agents_spec_lock(agents_dir):
        existing = agent_mod._read_spec_capped(path)
        if path.exists() and not isinstance(existing, dict):
            agent_mod.logger.error("Refusing to replace an unreadable Assistant template: %s", path)
            return False
        if isinstance(existing, dict):
            prompt = existing.get("prompt")
            if existing.get("name") != "kirocrew-assistant" or not (
                isinstance(prompt, str) and prompt.startswith(agent_mod._ASSISTANT_PROMPT_HEADER)
            ):
                agent_mod.logger.error(
                    "Refusing to overwrite %s: it was not written by the assistant "
                    "installer; move or rename it to let Kiro Crew manage this template",
                    path,
                )
                return False

        config = agent_mod.build_agent_config()
        config["name"] = "kirocrew-assistant"
        config["description"] = (
            "Personal assistant: everyday tasks, Kiro Crew setup and operation, and "
            "sourced recommendations for which goals deserve a crewmate."
        )
        docs_index = (Path(agent_mod.__file__).resolve().parent / "docs" / "README.md").as_posix()
        config["prompt"] = agent_mod._ASSISTANT_SYSTEM_PROMPT.replace("{docs_index}", docs_index)

        resources = [r for r in (config.get("resources") or []) if isinstance(r, str)]
        resources.extend(r for r in _assistant_skill_resources() if r not in resources)
        config["resources"] = resources

        _narrow_to_installed_default(config)
        _grant_assistant_guide_set(config)
        config["mcpServers"] = auto_approve._strip_ungoverned_auto_approve(
            config.get("mcpServers") or {}
        )
        auto_approve._write_derived_permissions(
            config, config.get("allowedTools"), _ASSISTANT_AGENT_FILENAME
        )

        try:
            model = KiroCrewConfig.load().agent.model or "auto"
        except Exception:
            model = "auto"
        config["model"] = model
        if isinstance(existing, dict) and _assistant_model_is_user_pinned():
            pinned = existing.get("model")
            if isinstance(pinned, str) and pinned.strip():
                config["model"] = pinned

        agent_mod._atomic_json_write(path, config)
    agent_mod.logger.info("Installed assistant agent config: %s", path)
    return True


def _bind_default_assistant_once() -> None:
    """Adopt the installed Assistant for the untouched reserved default member.

    The sidecar makes this a one-time adoption: an operator who later switches
    back to the ordinary template keeps that choice across rebuilds. Overlay
    bindings and non-default memory identities are never changed.
    """
    from kiro_crew.atomic_write import atomic_write
    from kiro_crew.config.loader import config_local_path, update_config_locked

    marker = agent_mod.config_dir() / "default_assistant_bound.json"

    def mark_done() -> None:
        atomic_write(marker, '{"version": 1}\n')

    def adopt(data: dict) -> dict | None:
        if marker.exists():
            return None
        overlay_binding = False

        def inspect_overlay(overlay: dict) -> None:
            nonlocal overlay_binding
            rows = overlay.get("agents", {})
            member = rows.get("default", {}) if isinstance(rows, dict) else {}
            overlay_agent = overlay.get("agent", {})
            custom_default = (
                isinstance(overlay_agent, dict)
                and "default_agent" in overlay_agent
                and overlay_agent["default_agent"] not in ("", "kirocrew")
            )
            overlay_binding = (
                (not data.get("agents") and (bool(rows) or custom_default))
                or not isinstance(member, dict)
                or any(key in member for key in ("kiro_agent", "memory_store", "member_id"))
            )

        update_config_locked(config_local_path(), mutate=inspect_overlay)
        rows = data.get("agents")
        if rows is None or rows == {}:
            ordinary = data.get("agent", {})
            template = (
                ordinary.get("default_agent", "kirocrew") if isinstance(ordinary, dict) else None
            )
            if template not in ("", "kirocrew") or overlay_binding:
                mark_done()
                return None
            rows = {"default": {"workspace": "default", "memory_store": "default"}}
            data["agents"] = rows
        member = rows.get("default") if isinstance(rows, dict) else None
        if (
            overlay_binding
            or not isinstance(member, dict)
            or member.get("kiro_agent", "kirocrew") not in ("", "kirocrew")
            or member.get("member_id")
            or member.get("memory_store", "default") != "default"
        ):
            mark_done()
            return None
        member["kiro_agent"] = "kirocrew-assistant"
        return data

    update_config_locked(mutate=adopt, after_write=mark_done)
