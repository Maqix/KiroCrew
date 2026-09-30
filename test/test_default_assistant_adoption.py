"""An existing default-member DM moves onto the adopted Assistant at a turn boundary."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest
from chat_test_helpers import _make_state, drain_background_tasks

from kiro_crew import execution_context as execution
from kiro_crew.config import loader
from kiro_crew.config.loader import KiroCrewAgentConfig, KiroCrewConfig, resolve_agent_bindings
from kiro_crew.context import ContextBuilder
from kiro_crew.dashboard import chat_handlers, chat_runner
from kiro_crew.members import DM_SLOT_MODE, member_slot_key
from kiro_crew.memory import MemoryStore
from kiro_crew.memory_stores import UnknownMemoryStore
from kiro_crew.providers.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from kiro_crew.session_agent_selection import (
    DEFAULT_ASSISTANT_TEMPLATE,
    plan_default_assistant_adoption,
    publish_default_assistant_adoption,
    resolve_session_agent_bindings,
)
from kiro_crew.skills import SkillsLoader

SLOT = member_slot_key("default")
KEY = f"dashboard:{SLOT}"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path))
    loader._invalidate_config_cache()
    execution._LIVE_EXECUTIONS.clear()
    execution._VOUCHED_EXECUTIONS.clear()
    config = KiroCrewConfig()
    config.agents = {"default": KiroCrewAgentConfig(kiro_agent="kirocrew")}
    monkeypatch.setattr(KiroCrewConfig, "load", classmethod(lambda cls: config))
    yield config
    execution._LIVE_EXECUTIONS.clear()
    execution._VOUCHED_EXECUTIONS.clear()


def _seed_pre_assistant_dm(config, key: str = KEY) -> execution.ExecutionContext:
    """The record the first turn wrote before the Assistant was adopted."""
    prior = execution.resolve_member_execution(config, "default")
    execution.bind_session_execution(key, prior)
    config.agents["default"].kiro_agent = DEFAULT_ASSISTANT_TEMPLATE
    return prior


def _resolved_template(config, key: str = KEY) -> str:
    return resolve_session_agent_bindings(resolve_agent_bindings, config, key, "default").kiro_agent


def test_prior_record_pins_old_template_and_resolution_does_not_mutate(cfg):
    prior = _seed_pre_assistant_dm(cfg)
    # The defect: config binds the Assistant, the existing record still wins.
    assert _resolved_template(cfg) == "kirocrew"
    assert _resolved_template(cfg, "dashboard:fresh") == DEFAULT_ASSISTANT_TEMPLATE
    # The read-only resolver (every caller, prewarm included) never migrates.
    assert execution.read_session_execution(KEY) == prior


def test_adoption_changes_only_the_template(cfg):
    prior = _seed_pre_assistant_dm(cfg)
    plan = plan_default_assistant_adoption(cfg, KEY)
    assert plan is not None and plan[0] == prior
    publish_default_assistant_adoption(KEY, plan)

    adopted = execution.read_session_execution(KEY)
    assert adopted.template_id == DEFAULT_ASSISTANT_TEMPLATE
    assert replace(adopted, template_id="kirocrew", selection_revision="") == replace(
        prior, selection_revision=""
    )
    bindings = resolve_session_agent_bindings(resolve_agent_bindings, cfg, KEY, "default")
    assert bindings.kiro_agent == DEFAULT_ASSISTANT_TEMPLATE
    assert bindings.memory_store_name == "default"
    assert bindings.selection_kind == "member"
    # Never vouched: the store came out of the session's own record.
    assert execution._live_key(KEY) not in execution._VOUCHED_EXECUTIONS
    assert plan_default_assistant_adoption(cfg, KEY) is None


@pytest.mark.parametrize(
    "shape",
    ["template-pick", "custom-record-template", "app", "config-custom", "config-v2", "no-record"],
)
def test_adoption_refuses_every_other_shape(cfg, shape):
    prior = execution.resolve_member_execution(cfg, "default")
    if shape == "template-pick":
        prior = prior.with_template("kirocrew", "default")
    elif shape == "custom-record-template":
        prior = replace(prior, template_id="my-template")
    elif shape == "app":
        prior = replace(prior, app="some-app")
    if shape != "no-record":
        execution.bind_session_execution(KEY, prior)
    cfg.agents["default"].kiro_agent = (
        "my-template" if shape == "config-custom" else DEFAULT_ASSISTANT_TEMPLATE
    )
    if shape == "config-v2":
        cfg.agents["default"].member_id = "id-default"
    assert plan_default_assistant_adoption(cfg, KEY) is None


def test_other_member_dm_is_never_adopted(cfg):
    cfg.agents["alice"] = KiroCrewAgentConfig(kiro_agent="kirocrew")
    key = f"dashboard:{member_slot_key('alice')}"
    execution.bind_session_execution(key, execution.resolve_member_execution(cfg, "alice"))
    cfg.agents["alice"].kiro_agent = DEFAULT_ASSISTANT_TEMPLATE
    cfg.agents["default"].kiro_agent = DEFAULT_ASSISTANT_TEMPLATE
    assert plan_default_assistant_adoption(cfg, key) is None


def test_concurrent_record_change_wins_the_compare_and_set(cfg):
    prior = _seed_pre_assistant_dm(cfg)
    plan = plan_default_assistant_adoption(cfg, KEY)
    concurrent = prior.with_template("kirocrew-conductor", "kirocrew-conductor")
    execution.bind_session_execution(KEY, concurrent, replace_existing=True, expected=prior)
    with pytest.raises(UnknownMemoryStore):
        publish_default_assistant_adoption(KEY, plan)
    assert execution.read_session_execution(KEY) == concurrent


def _dm_slot(tmp_path, **kwargs):
    state = _make_state(tmp_path, **kwargs)
    state.subagents = None
    slot = state.get_or_create_slot(SLOT, agent="default", mode=DM_SLOT_MODE)
    return state, slot


@pytest.mark.asyncio
async def test_idle_or_restored_runtime_is_discarded_with_replay(cfg, tmp_path):
    _seed_pre_assistant_dm(cfg)
    state, slot = _dm_slot(tmp_path)
    state.sessions.discard_conversation = AsyncMock(return_value=True)

    assert await chat_runner._adopt_default_assistant_at_boundary(state, slot, KEY)
    # Sid cleared + replay kept: the old native conversation cannot resume.
    state.sessions.discard_conversation.assert_awaited_once_with(KEY, skip_if_busy=True)
    assert _resolved_template(cfg) == DEFAULT_ASSISTANT_TEMPLATE


@pytest.mark.asyncio
async def test_busy_sibling_is_never_interrupted_and_record_rolls_back(cfg, tmp_path):
    prior = _seed_pre_assistant_dm(cfg)
    state, slot = _dm_slot(tmp_path)
    state.sessions.discard_conversation = AsyncMock(return_value=False)

    assert not await chat_runner._adopt_default_assistant_at_boundary(state, slot, KEY)
    assert execution.read_session_execution(KEY) == prior
    assert _resolved_template(cfg) == "kirocrew"
    # Retryable: the next boundary plans the same adoption again.
    assert plan_default_assistant_adoption(cfg, KEY) is not None


@pytest.mark.asyncio
async def test_teardown_failure_rolls_back_truthfully(cfg, tmp_path):
    prior = _seed_pre_assistant_dm(cfg)
    state, slot = _dm_slot(tmp_path)
    state.sessions.discard_conversation = AsyncMock(side_effect=RuntimeError("shutdown"))

    assert not await chat_runner._adopt_default_assistant_at_boundary(state, slot, KEY)
    assert execution.read_session_execution(KEY) == prior


@pytest.mark.asyncio
async def test_attached_children_or_starting_prewarm_defer(cfg, tmp_path, monkeypatch):
    prior = _seed_pre_assistant_dm(cfg)
    state, slot = _dm_slot(tmp_path)
    state.sessions.discard_conversation = AsyncMock(return_value=True)
    monkeypatch.setattr(chat_runner, "subagents_attached_async", AsyncMock(return_value=True))
    assert not await chat_runner._adopt_default_assistant_at_boundary(state, slot, KEY)

    monkeypatch.setattr(chat_runner, "subagents_attached_async", AsyncMock(return_value=False))
    starting = asyncio.get_running_loop().create_future()
    slot._eager_spawn_task = starting
    assert not await chat_runner._adopt_default_assistant_at_boundary(state, slot, KEY)
    starting.cancel()
    state.sessions.discard_conversation.assert_not_awaited()
    assert execution.read_session_execution(KEY) == prior


@pytest.mark.asyncio
async def test_fresh_or_template_slot_touches_nothing(cfg, tmp_path):
    cfg.agents["default"].kiro_agent = DEFAULT_ASSISTANT_TEMPLATE
    state, slot = _dm_slot(tmp_path)
    assert not await chat_runner._adopt_default_assistant_at_boundary(state, slot, KEY)
    template_slot = state.get_or_create_slot("ordinary", agent="default")
    execution.bind_session_execution(
        "dashboard:ordinary",
        execution.resolve_member_execution(cfg, "default"),
    )
    assert not await chat_runner._adopt_default_assistant_at_boundary(
        state, template_slot, "dashboard:ordinary"
    )
    state.sessions.discard_conversation.assert_not_awaited()


def _turn(state, monkeypatch):
    provider = MagicMock()

    async def stream(*args, **kwargs):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield LLMEvent(kind=EVENT_COMPLETE)

    provider.stream = stream
    state.context_builder.build_message = MagicMock(return_value=("task", None))
    state.context_builder.ensure_store = AsyncMock(return_value=object())
    state.sessions.get_or_create = AsyncMock(return_value=(provider, True, False))
    state.sessions.consume_replay_suppression = MagicMock(return_value=False)
    state.sessions.record_failure = AsyncMock()
    monkeypatch.setattr(chat_runner, "title_then_refresh", AsyncMock())
    monkeypatch.setattr(chat_runner, "generate_session_summary", AsyncMock())
    monkeypatch.setattr(chat_handlers, "schedule_eager_spawn", lambda *a, **kw: None)


@pytest.mark.asyncio
@pytest.mark.parametrize("idle", [True, False])
async def test_next_turn_allocates_the_template_the_record_names(cfg, tmp_path, monkeypatch, idle):
    _seed_pre_assistant_dm(cfg)
    builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "workspace"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )
    state, slot = _dm_slot(tmp_path, context_builder=builder)
    _turn(state, monkeypatch)
    order: list[str] = []
    state.sessions.discard_conversation = AsyncMock(
        side_effect=lambda *a, **kw: order.append("discard") or idle
    )
    get_or_create = state.sessions.get_or_create
    get_or_create.side_effect = lambda *a, **kw: (
        order.append("allocate") or get_or_create.return_value
    )

    slot.append("user", "hello")
    await asyncio.wait_for(chat_runner._run_chat(state, slot, "hello"), 10)
    await asyncio.wait_for(drain_background_tasks(state), 10)

    expected = DEFAULT_ASSISTANT_TEMPLATE if idle else "kirocrew"
    assert order == ["discard", "allocate"]
    assert get_or_create.await_args.kwargs["agent"] == expected
    record = execution.read_session_execution(KEY)
    assert record.template_id == expected
    assert (record.store.store_id, record.selection_kind) == ("default", "member")
