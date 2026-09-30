"""``session/set_mode`` never bricks a session over a skill-view alias.

kiro-cli 2.25.0 and 2.26.0 reload ``~/.kiro/agents`` on a data write and never on
a rename, so an alias ``atomic_write`` renamed into place after the process
started answers ``Mode '<alias>' not found`` while the file is on disk. The
runtime re-prepares the projection right before ``set_mode``, so any change to a
source spec since spawn names exactly such an alias.

These drive the REAL reader and ``_send_and_await`` against a fake kiro-cli that
answers ``set_mode`` from the set of names it has "loaded", so the name
translation, the error mapping and the fallback are all the product's own.
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_update_provider import _UNALLOCATABLE_PID

from kiro_crew.acp import runtime as runtime_mod
from kiro_crew.acp.runtime import AcpRuntime
from kiro_crew.acp.session_handle import AcpModeNotFound, AcpRuntimeError
from kiro_crew.acp.skill_projection import NativeSkillProjection
from kiro_crew.acp.types import METHOD_SET_MODE

STALE_ALIAS = "kirocrew-skill-view-" + "a" * 24
FRESH_ALIAS = "kirocrew-skill-view-" + "b" * 24


class _FakeKiro:
    """Answers awaited requests the way kiro-cli does, from what it has loaded."""

    def __init__(self, reader: asyncio.StreamReader, loaded: set[str]) -> None:
        self.reader = reader
        self.loaded = loaded
        self.set_modes: list[str] = []
        self.reload_after_misses: int | None = None
        self.other_error: dict | None = None
        self._misses = 0

    def write(self, data: bytes) -> None:
        frame = json.loads(data)
        if "id" not in frame:
            return
        method = frame.get("method")
        if method == METHOD_SET_MODE:
            mode = frame["params"]["modeId"]
            self.set_modes.append(mode)
            if self.other_error is not None:
                self._answer(frame["id"], error=self.other_error)
            elif mode in self.loaded:
                self._answer(frame["id"], result={})
            else:
                self._misses += 1
                if (
                    self.reload_after_misses is not None
                    and self._misses >= self.reload_after_misses
                ):
                    self.loaded.add(FRESH_ALIAS)
                self._answer(
                    frame["id"],
                    error={
                        "code": -32603,
                        "message": "Internal error",
                        "data": f"Mode '{mode}' not found",
                    },
                )
        else:
            self._answer(frame["id"], result={})

    def _answer(self, req_id: int, **body: object) -> None:
        self.reader.feed_data(
            (json.dumps({"jsonrpc": "2.0", "id": req_id, **body}) + "\n").encode()
        )


def _runtime(loaded: set[str]) -> tuple[AcpRuntime, _FakeKiro]:
    rt = AcpRuntime(work_dir="/tmp")
    reader = asyncio.StreamReader()
    kiro = _FakeKiro(reader, loaded)
    proc = MagicMock()
    proc.stdout = reader
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock(side_effect=kiro.write)
    proc.stdin.drain = AsyncMock()
    proc.returncode = None
    proc.pid = _UNALLOCATABLE_PID
    rt._process = proc
    rt._pid = _UNALLOCATABLE_PID
    rt._initialized = True
    rt._native_skill_projection = NativeSkillProjection({"ops": FRESH_ALIAS})
    rt._spawn_skill_projection = NativeSkillProjection(
        {"ops": FRESH_ALIAS}, authored={"ops": GRANTS, "dev": GRANTS}
    )
    return rt, kiro


GRANTS = "grants-at-spawn"


@pytest.fixture(autouse=True)
def unchanged_authored_spec(monkeypatch):
    """The authored spec on disk grants what it did at spawn, unless a test says not."""
    from kiro_crew.acp import skill_projection

    monkeypatch.setattr(skill_projection, "authored_identity_now", lambda _wd, _agent: GRANTS)


@pytest.fixture(autouse=True)
def announced(monkeypatch):
    """Record the in-place rescan nudges instead of touching an agents directory."""
    from kiro_crew.acp import skill_projection

    seen: list[str] = []
    monkeypatch.setattr(skill_projection, "announce_alias", seen.append)
    return seen


@pytest.fixture
def no_retry_wait(monkeypatch):
    monkeypatch.setattr(runtime_mod, "_PROJECTED_MODE_RETRY_DELAYS_SECS", (0.0, 0.0))


async def _with_reader(rt: AcpRuntime, coro):
    task = asyncio.ensure_future(rt._reader_loop())
    await asyncio.sleep(0)
    try:
        return await asyncio.wait_for(coro, timeout=10)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def _activate(
    rt: AcpRuntime, projection: NativeSkillProjection, *, snapshot: object = None
) -> None:
    """Run the real bracket with the re-preparation returning *projection*.

    *snapshot* stands in for the derived-spec bracket's verified snapshot: not
    ``None`` means *mode_agent*'s spec is freshness-checked."""
    rt.terminate_session = AsyncMock()  # type: ignore[method-assign]
    with (
        patch(
            "kiro_crew.acp.skill_projection.prepare_native_skill_projection",
            return_value=projection,
        ),
        patch("kiro_crew.agent.require_unchanged_derived_spec", return_value=None),
    ):
        await rt._activate_mode_bracketed(
            "s1", "ops", budget=5.0, payload_snapshot=snapshot, wire_registered=True
        )


@pytest.mark.asyncio
async def test_a_freshly_published_alias_the_host_never_loads_falls_back_to_the_authored_agent(
    no_retry_wait, caplog
):
    """The incident: the bracket re-prepares, the view changed since spawn, the
    new alias was renamed into place, and kiro-cli never loads it. The session
    must come up on the authored agent -- not be terminated with "is not
    installed" on every turn."""
    rt, kiro = _runtime({STALE_ALIAS, "ops"})
    caplog.set_level(logging.WARNING, logger="kiro_crew.acp.runtime")

    await _with_reader(rt, _activate(rt, NativeSkillProjection({"ops": FRESH_ALIAS})))

    rt.terminate_session.assert_not_awaited()
    retries = len(runtime_mod._PROJECTED_MODE_RETRY_DELAYS_SECS)
    assert kiro.set_modes == [FRESH_ALIAS] * (1 + retries) + ["ops"]
    assert any(
        "runs the authored agent instead" in r.getMessage() and FRESH_ALIAS in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_an_alias_the_host_loads_after_its_reload_is_used_without_falling_back(
    no_retry_wait, caplog
):
    """The publication's in-place rewrite makes kiro-cli reload; a retry after
    that lands on the alias, so bounded skill discovery is kept."""
    rt, kiro = _runtime({"ops"})
    kiro.reload_after_misses = 1
    caplog.set_level(logging.WARNING, logger="kiro_crew.acp.runtime")

    await _with_reader(rt, _activate(rt, NativeSkillProjection({"ops": FRESH_ALIAS})))

    rt.terminate_session.assert_not_awaited()
    assert kiro.set_modes == [FRESH_ALIAS, FRESH_ALIAS]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_a_missing_authored_agent_still_fails_with_the_repair_sentence(no_retry_wait):
    """The fallback is for a projection miss only. When the authored spec is
    gone too, the old actionable error stands and the session is torn down."""
    rt, kiro = _runtime(set())

    with pytest.raises(AcpModeNotFound) as excinfo:
        await _with_reader(rt, _activate(rt, NativeSkillProjection({"ops": FRESH_ALIAS})))

    assert excinfo.value.mode_id == "ops"
    assert "kirocrew setup --agent-only" in str(excinfo.value)
    rt.terminate_session.assert_awaited_once_with("s1")
    assert kiro.set_modes[-1] == "ops"


@pytest.mark.asyncio
async def test_without_a_projection_a_missing_mode_is_not_retried(no_retry_wait):
    """No alias was involved, so there is nothing to wait for or fall back from."""
    rt, kiro = _runtime(set())
    rt._native_skill_projection = None

    with pytest.raises(AcpModeNotFound):
        await _with_reader(rt, _activate(rt, rt._native_skill_projection))

    assert kiro.set_modes == ["ops"]


@pytest.mark.asyncio
async def test_any_other_set_mode_error_propagates_without_a_retry(no_retry_wait):
    rt, kiro = _runtime({FRESH_ALIAS})
    kiro.other_error = {"code": -32603, "message": "Internal error", "data": "boom"}

    with pytest.raises(AcpRuntimeError) as excinfo:
        await _with_reader(rt, _activate(rt, rt._native_skill_projection))

    assert not isinstance(excinfo.value, AcpModeNotFound)
    assert kiro.set_modes == [FRESH_ALIAS]


def test_the_retry_schedule_outlasts_kiro_clis_reload_debounce_and_stays_bounded():
    """kiro-cli reloads 500 ms after the LAST data write (a trailing debounce,
    then a full rescan); measured on 2.26.0 a written alias became selectable
    between 0.7 s and 1.5 s after it was published. No wait may be shorter than
    the debounce, the schedule must cover that window with room to spare, and
    it must stay a small fraction of the set_mode budget."""
    delays = runtime_mod._PROJECTED_MODE_RETRY_DELAYS_SECS
    assert delays and min(delays) >= 0.5
    assert 2.5 <= sum(delays) <= 5.0


@pytest.fixture
def counted(monkeypatch):
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(runtime_mod, "emit_counter", lambda name, attrs: seen.append((name, attrs)))
    return seen


SIBLING_SPAWN_ALIAS = "kirocrew-skill-view-" + "c" * 24
SIBLING_FRESH_ALIAS = "kirocrew-skill-view-" + "d" * 24


def _listed(rt: AcpRuntime, *ids: str) -> list[str]:
    """What ``availableModes`` says after the runtime's inbound translation."""
    frame = rt._native_skill_projection.frame({"availableModes": [{"id": i} for i in ids]})
    return [mode["id"] for mode in frame["availableModes"]]


@pytest.mark.asyncio
async def test_a_changed_view_sends_the_fresh_alias_and_never_the_spawn_one(no_retry_wait, counted):
    """The spawn alias may hold a generation of the spec an edit has since removed
    a server or an auto-approval from, so set_mode names the fresh alias; a host
    that loaded it answers first time and nothing is counted."""
    rt, kiro = _runtime({STALE_ALIAS, FRESH_ALIAS})
    spawn = NativeSkillProjection({"ops": STALE_ALIAS}, authored={"ops": GRANTS})
    rt._native_skill_projection = spawn
    rt._spawn_skill_projection = spawn
    fresh = NativeSkillProjection({"ops": FRESH_ALIAS})

    await _with_reader(rt, _activate(rt, fresh))

    assert kiro.set_modes == [FRESH_ALIAS]
    assert rt._native_skill_projection is fresh
    assert counted == []
    rt.terminate_session.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", [None, object()], ids=["plain", "freshness-checked"])
async def test_a_changed_view_the_host_never_loads_ends_on_the_authored_agent(
    no_retry_wait, counted, snapshot
):
    """Even with the spawn alias still loaded, a changed view never falls back to
    it: after the retries the session runs the authored agent, for a
    freshness-checked (derived) agent exactly as for any other."""
    rt, kiro = _runtime({STALE_ALIAS, "ops"})
    spawn = NativeSkillProjection({"ops": STALE_ALIAS}, authored={"ops": GRANTS})
    rt._native_skill_projection = spawn
    rt._spawn_skill_projection = spawn

    await _with_reader(
        rt, _activate(rt, NativeSkillProjection({"ops": FRESH_ALIAS}), snapshot=snapshot)
    )

    retries = len(runtime_mod._PROJECTED_MODE_RETRY_DELAYS_SECS)
    assert kiro.set_modes == [FRESH_ALIAS] * (1 + retries) + ["ops"]
    assert STALE_ALIAS not in kiro.set_modes
    assert [a["outcome"] for _n, a in counted] == ["authored_agent"]
    rt.terminate_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_aliases_the_host_loaded_earlier_stay_selectable_after_a_fresh_view(
    no_retry_wait, counted
):
    """Adopting a projection that renamed a SIBLING must not hide the sibling:
    the host still lists it under its spawn alias, and ``_mode_available`` reads
    ``availableModes`` through the adopted projection. Recognition survives a
    second adoption too."""
    rt, kiro = _runtime({STALE_ALIAS, SIBLING_SPAWN_ALIAS})
    spawn = NativeSkillProjection(
        {"ops": STALE_ALIAS, "dev": SIBLING_SPAWN_ALIAS}, authored={"ops": GRANTS, "dev": GRANTS}
    )
    rt._spawn_skill_projection = spawn
    rt._native_skill_projection = spawn

    for _ in range(2):
        await _with_reader(
            rt,
            _activate(rt, NativeSkillProjection({"ops": STALE_ALIAS, "dev": SIBLING_FRESH_ALIAS})),
        )
        assert _listed(rt, STALE_ALIAS, SIBLING_SPAWN_ALIAS) == ["ops", "dev"]
        assert rt._mode_available(
            "dev",
            {
                "modes": rt._native_skill_projection.frame(
                    {"availableModes": [{"id": SIBLING_SPAWN_ALIAS}]}
                )
            },
        )

    assert kiro.set_modes == [STALE_ALIAS, STALE_ALIAS]
    assert counted == []


@pytest.mark.asyncio
async def test_an_agent_that_fell_back_stays_listed_for_later_session_starts(no_retry_wait):
    """After the authored fallback the host may list the agent only under its
    authored id; it stays selectable (once), while unprojected host agents stay
    hidden."""
    rt, kiro = _runtime({"ops"})

    await _with_reader(rt, _activate(rt, NativeSkillProjection({"ops": FRESH_ALIAS})))

    assert kiro.set_modes[-1] == "ops"
    assert _listed(rt, "ops") == ["ops"]
    assert _listed(rt, FRESH_ALIAS, "ops", "kiro_default") == ["ops"]
    assert rt._mode_available(
        "ops", {"modes": rt._native_skill_projection.frame({"availableModes": [{"id": "ops"}]})}
    )


@pytest.mark.asyncio
async def test_a_re_preparation_that_cannot_run_runs_the_authored_agent(no_retry_wait, counted):
    """``None`` (the alias lock was busy): nothing proves the last alias still says
    what the spec says now, so the session runs the authored agent; the view stays
    for inbound frames."""
    rt, kiro = _runtime({STALE_ALIAS, "ops"})
    spawn = NativeSkillProjection({"ops": STALE_ALIAS}, authored={"ops": GRANTS})
    rt._native_skill_projection = spawn

    await _with_reader(rt, _activate(rt, None))

    assert kiro.set_modes == ["ops"]
    assert rt._native_skill_projection is spawn
    assert counted == [(runtime_mod.SKILL_VIEW_FALLBACKS, {"outcome": "authored_unprepared"})]


@pytest.mark.asyncio
async def test_every_rung_below_the_first_try_is_counted(no_retry_wait, counted):
    rt, kiro = _runtime({"ops"})
    kiro.reload_after_misses = 1
    await _with_reader(rt, _activate(rt, NativeSkillProjection({"ops": FRESH_ALIAS})))
    assert counted == [(runtime_mod.SKILL_VIEW_FALLBACKS, {"outcome": "loaded_after_retry"})]

    counted.clear()
    rt, kiro = _runtime({"ops"})
    await _with_reader(rt, _activate(rt, NativeSkillProjection({"ops": FRESH_ALIAS})))
    assert counted == [(runtime_mod.SKILL_VIEW_FALLBACKS, {"outcome": "authored_agent"})]


@pytest.mark.asyncio
async def test_a_fallback_that_itself_fails_is_not_counted(no_retry_wait, counted):
    """The outcome is counted once its set_mode succeeded: a session torn down
    because the authored agent is missing too never ran it."""
    rt, _kiro = _runtime(set())

    with pytest.raises(AcpModeNotFound):
        await _with_reader(rt, _activate(rt, NativeSkillProjection({"ops": FRESH_ALIAS})))

    assert counted == []


@pytest.mark.asyncio
async def test_a_retry_never_re_translates_through_a_projection_swapped_in_meanwhile(
    no_retry_wait, counted
):
    """The retry sleeps; a concurrent session start can replace the runtime's
    projection in that window with an older preparation whose alias the host DID
    load and which still carries grants an edit removed. Every attempt sends the
    alias this bracket prepared, then the authored agent -- never that one."""
    rt, kiro = _runtime({STALE_ALIAS, "ops"})
    original = rt._send_and_await

    async def send_then_swap(*args, **kwargs):
        try:
            return await original(*args, **kwargs)
        finally:
            rt._native_skill_projection = NativeSkillProjection(
                {"ops": STALE_ALIAS}, authored={"ops": GRANTS}
            )

    rt._send_and_await = send_then_swap  # type: ignore[method-assign]

    await _with_reader(rt, _activate(rt, NativeSkillProjection({"ops": FRESH_ALIAS})))

    retries = len(runtime_mod._PROJECTED_MODE_RETRY_DELAYS_SECS)
    assert kiro.set_modes == [FRESH_ALIAS] * (1 + retries) + ["ops"]
    assert STALE_ALIAS not in kiro.set_modes
    assert [a["outcome"] for _n, a in counted] == ["authored_agent"]


def test_recognised_aliases_stay_bounded_and_keep_the_spawn_ones_first(monkeypatch):
    """A process whose specs are edited without end publishes an alias per edit;
    what a projection keeps translating is bounded, and the spawn aliases -- the
    ones the host is guaranteed to hold -- are recognised first."""
    from kiro_crew.acp import skill_projection

    assert skill_projection._RECOGNISED_ALIASES_MAX == 1024
    monkeypatch.setattr(skill_projection, "_RECOGNISED_ALIASES_MAX", 8)
    spawn = NativeSkillProjection(
        {"ops": STALE_ALIAS, "dev": SIBLING_SPAWN_ALIAS}, authored={"ops": GRANTS, "dev": GRANTS}
    )
    earlier = NativeSkillProjection({"ops": FRESH_ALIAS})
    for n in range(50):
        earlier._recognised[f"kirocrew-skill-view-{n:024d}"] = "ops"
    current = NativeSkillProjection({"ops": "kirocrew-skill-view-" + "e" * 24})

    current.recognise(spawn)
    current.recognise(earlier)

    assert len(current._recognised) == 8
    assert current._recognised[STALE_ALIAS] == "ops"
    assert current._recognised[SIBLING_SPAWN_ALIAS] == "dev"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [True, False], ids=["grants-changed", "unchanged"])
async def test_the_authored_fallback_is_refused_when_the_spec_changed_since_spawn(
    no_retry_wait, counted, changed, monkeypatch
):
    """kiro-cli does not reload a spec replaced by rename, so after the fresh alias
    misses, its authored copy may be the one from spawn. When what the spec grants
    changed since then, running it could use a removed permission: the start fails,
    naming the fix. An unchanged grant identity (a relaunch re-stamping env values)
    still falls back."""
    from kiro_crew.acp import skill_projection

    now = "grants-now" if changed else "grants-at-spawn"
    monkeypatch.setattr(skill_projection, "authored_identity_now", lambda _wd, _agent: now)
    rt, kiro = _runtime({STALE_ALIAS, "ops"})
    spawn = NativeSkillProjection({"ops": STALE_ALIAS}, authored={"ops": GRANTS})
    rt._spawn_skill_projection = spawn
    rt._native_skill_projection = spawn
    # The preparation's own record is deliberately stale: the fallback reads disk.
    fresh = NativeSkillProjection({"ops": FRESH_ALIAS}, authored={"ops": GRANTS})

    if changed:
        with pytest.raises(AcpRuntimeError, match="Restart the gateway"):
            await _with_reader(rt, _activate(rt, fresh))
        assert "ops" not in kiro.set_modes and STALE_ALIAS not in kiro.set_modes
        assert [a["outcome"] for _n, a in counted] == ["refused_changed_spec"]
        rt.terminate_session.assert_awaited_once_with("s1")
    else:
        await _with_reader(rt, _activate(rt, fresh))
        assert kiro.set_modes[-1] == "ops"
        assert [a["outcome"] for _n, a in counted] == ["authored_agent"]


@pytest.mark.asyncio
@pytest.mark.parametrize("now", ["grants-now", "grants-at-spawn"])
async def test_an_unprepared_view_reads_the_authored_spec_before_falling_back(
    no_retry_wait, counted, monkeypatch, now
):
    from kiro_crew.acp import skill_projection

    monkeypatch.setattr(skill_projection, "authored_identity_now", lambda _wd, _agent: now)
    rt, kiro = _runtime({STALE_ALIAS, "ops"})
    spawn = NativeSkillProjection({"ops": STALE_ALIAS}, authored={"ops": GRANTS})
    rt._spawn_skill_projection = spawn
    rt._native_skill_projection = spawn

    if now == "grants-now":
        with pytest.raises(AcpRuntimeError, match="Restart the gateway"):
            await _with_reader(rt, _activate(rt, None))
        assert kiro.set_modes == []
    else:
        await _with_reader(rt, _activate(rt, None))
        assert kiro.set_modes == ["ops"]
        assert [a["outcome"] for _n, a in counted] == ["authored_unprepared"]


@pytest.mark.asyncio
async def test_a_miss_forces_a_rescan_before_each_retry(no_retry_wait, announced, monkeypatch):
    """A miss triggers no reload in kiro-cli, and the rename that published the
    alias is invisible to its watcher; the retry is preceded by a same-bytes
    in-place rewrite of the alias, which is a data write it DOES reload on."""
    from kiro_crew.acp import skill_projection

    rt, kiro = _runtime({"ops"})

    def rescan(alias):
        announced.append(alias)
        kiro.loaded.add(alias)

    monkeypatch.setattr(skill_projection, "announce_alias", rescan)
    await _with_reader(rt, _activate(rt, NativeSkillProjection({"ops": FRESH_ALIAS})))

    assert announced == [FRESH_ALIAS]
    assert kiro.set_modes == [FRESH_ALIAS, FRESH_ALIAS]


@pytest.mark.asyncio
async def test_a_revoked_auto_approval_is_never_usable_on_a_rename_blind_host(
    no_retry_wait, monkeypatch
):
    """The owner's case end to end, on the real grant identity: the spec loses an
    auto-approval after spawn, the host never reloads (rename-blind, and the
    rescan nudge changes nothing), so its only copies are the spawn alias and the
    spawn-time authored spec -- both carrying the revoked grant. Neither is ever
    activated: the start fails and names the restart."""
    from kiro_crew.acp import skill_projection

    before = {"name": "ops", "tools": ["@srv"], "allowedTools": ["@srv/delete"]}
    after = {"name": "ops", "tools": ["@srv"], "allowedTools": []}
    monkeypatch.setattr(
        skill_projection,
        "authored_identity_now",
        lambda _wd, _agent: skill_projection.authored_grant_identity(after),
    )
    rt, kiro = _runtime({STALE_ALIAS, "ops"})
    spawn = NativeSkillProjection(
        {"ops": STALE_ALIAS}, authored={"ops": skill_projection.authored_grant_identity(before)}
    )
    rt._spawn_skill_projection = spawn
    rt._native_skill_projection = spawn
    fresh = NativeSkillProjection(
        {"ops": FRESH_ALIAS}, authored={"ops": skill_projection.authored_grant_identity(after)}
    )

    with pytest.raises(AcpRuntimeError, match="Restart the gateway"):
        await _with_reader(rt, _activate(rt, fresh))

    assert STALE_ALIAS not in kiro.set_modes
    assert "ops" not in kiro.set_modes


@pytest.mark.asyncio
async def test_a_revocation_landing_during_the_authored_switch_tears_the_session_down(
    no_retry_wait, counted, monkeypatch
):
    """The grants are re-read after the authored set_mode returns too: a
    revocation landing while kiro-cli activated the old copy leaves it running
    permissions the spec has since dropped, so the session is ended."""
    from kiro_crew.acp import skill_projection

    reads = iter(["grants-at-spawn", "grants-now"])
    monkeypatch.setattr(skill_projection, "authored_identity_now", lambda _wd, _a: next(reads))
    rt, kiro = _runtime({STALE_ALIAS, "ops"})
    spawn = NativeSkillProjection({"ops": STALE_ALIAS}, authored={"ops": GRANTS})
    rt._spawn_skill_projection = spawn
    rt._native_skill_projection = spawn

    with pytest.raises(AcpRuntimeError, match="Restart the gateway"):
        await _with_reader(rt, _activate(rt, NativeSkillProjection({"ops": FRESH_ALIAS})))

    assert kiro.set_modes[-1] == "ops"
    rt.terminate_session.assert_awaited_once_with("s1")
    assert [a["outcome"] for _n, a in counted] == ["refused_changed_spec"]


@pytest.mark.asyncio
async def test_an_agent_with_no_spawn_baseline_is_refused_the_authored_fallback(no_retry_wait):
    """An agent added after spawn has no grant identity recorded then, so nothing
    proves the copy kiro-cli holds is current: the fallback is refused, not run."""
    rt, kiro = _runtime({"ops"})
    rt._spawn_skill_projection = NativeSkillProjection({}, authored={})

    with pytest.raises(AcpRuntimeError, match="or was added"):
        await _with_reader(rt, _activate(rt, NativeSkillProjection({"ops": FRESH_ALIAS})))

    assert "ops" not in kiro.set_modes
