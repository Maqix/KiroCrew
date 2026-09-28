"""The first-run state file: several writers, one file, no lost change."""

from __future__ import annotations

import threading

from kiro_crew import first_run


def test_concurrent_writers_keep_each_others_keys(monkeypatch) -> None:
    first_run.record_slot("chat-1-1")
    real_read = first_run.read_state
    barrier = threading.Barrier(8, timeout=10)

    def _slow_read():
        state = real_read()
        try:
            # Every writer has read before any writes: the lost-update window.
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return state

    monkeypatch.setattr(first_run, "read_state", _slow_read)
    # Short: under the lock the barrier cannot fill, so each reader waits it out.
    monkeypatch.setattr(barrier, "_timeout", 0.05)
    writers = [
        threading.Thread(
            target=first_run.update_state,
            args=(lambda state, i=i: state.__setitem__(f"k{i}", i),),
        )
        for i in range(8)
    ]
    for t in writers:
        t.start()
    for t in writers:
        t.join(timeout=30)
    monkeypatch.setattr(first_run, "read_state", real_read)
    state = first_run.read_state()
    assert {f"k{i}" for i in range(8)} <= set(state)
    assert state["slot"] == "chat-1-1"


def test_a_change_that_returns_false_writes_nothing(monkeypatch) -> None:
    first_run.record_slot("chat-1-1")
    writes: list = []
    monkeypatch.setattr(first_run, "write_state", lambda state: writes.append(state))
    assert first_run.update_state(lambda state: False)["slot"] == "chat-1-1"
    assert writes == []


def test_the_writers_keep_the_other_keys() -> None:
    first_run.record_home_choice("cloud", region="eu-west-1")
    first_run.record_slot("chat-1-1")
    first_run.mark_stage("import")
    first_run.record_main("chat-1-1")
    first_run.write_handoff_owed({"chat-1-1": {"chat-2-2": {"kind": "handoff_done"}}})
    first_run.mark_stage("import")
    first_run.write_handoff_owed({})
    state = first_run.read_state()
    assert state["home"] == {"choice": "cloud", "region": "eu-west-1"}
    assert state["slot"] == state["main"] == "chat-1-1"
    assert list(state["stages"]) == ["import"]
    assert "handoff_owed" not in state
