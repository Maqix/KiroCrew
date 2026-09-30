"""Per-session dedup and aggregate budget over prompt image blocks.

The per-image caps (``imaging.py``) bound one image; this layer bounds what a
session inlines in TOTAL, because kiro-cli replays every inlined image on every
later turn and the backend refuses the request body once the replay crosses its
ceiling. Four properties are pinned, in the order the work item states them:

(a) the same bytes offered twice in one session yield one image block, and the
    second offer's marker reads ``sent earlier``;
(b) the ledger survives a gateway restart -- it is on the session record and a
    fresh ``SessionMap`` read from disk still dedups;
(c) N images crossing the per-prompt budget yield blocks up to the cap and text
    markers after it, and the running total is recorded on the ledger;
(d) the existing per-image caps are untouched.

Plus the wiring: the ACP session handle and the direct client both apply the
layer after the builder, and no host-side annotation reaches the wire.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
from pathlib import Path

import pytest

from kiro_crew import image_ledger
from kiro_crew.acp.client import AcpClient
from kiro_crew.acp.prompt_blocks import (
    MAX_IMAGE_B64_BYTES,
    MAX_IMAGE_EDGE_PX,
    build_prompt_blocks,
)
from kiro_crew.acp.runtime import AcpRuntime
from kiro_crew.acp.session_handle import AcpRuntimeDead, AcpSessionHandle
from kiro_crew.image_ledger import (
    IMAGE_BLOCK_SOURCE_KEY,
    MAX_LEDGER_HASHES,
    MAX_PROMPT_IMAGE_B64_BYTES,
    MAX_PROMPT_IMAGE_BLOCKS,
    MAX_SESSION_IMAGE_B64_BYTES,
    OVER_BUDGET_MARKER,
    SENT_EARLIER_MARKER,
    UNNAMED_OVER_BUDGET_NOTE,
    UNNAMED_SENT_EARLIER_NOTE,
    SessionImageBudget,
    apply_image_budget,
    empty_ledger,
    image_digest,
    normalize_ledger,
)
from kiro_crew.session_map import SessionMap

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs" / "system-specs" / "modules" / "acp-client.md"

MIB = 1024 * 1024


def _data(seed: int, size: int = 120) -> str:
    """A deterministic base64 payload of exactly ``size`` characters, distinct per seed."""
    raw = bytes([seed % 256]) * (size * 3 // 4)
    out = base64.b64encode(raw).decode("ascii")
    assert len(out) == size, (len(out), size)
    return out


def _image(seed: int, name: str = "", path: str = "", size: int = 120) -> dict:
    block = {"type": "image", "data": _data(seed, size), "mimeType": "image/png"}
    if name or path:
        block[IMAGE_BLOCK_SOURCE_KEY] = {"name": name, "path": path}
    return block


def _text(*names: str) -> dict:
    return {"type": "text", "text": "look: " + " ".join(f"[image: {n}]" for n in names)}


def _image_blocks(blocks: list[dict]) -> list[dict]:
    return [b for b in blocks if b.get("type") == "image"]


def _png_bytes(seed: int) -> bytes:
    pil = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    pil.new("RGB", (4, 4), (seed % 256, 90, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _png(tmp_path: Path, name: str, seed: int = 1) -> Path:
    p = tmp_path / name
    p.write_bytes(_png_bytes(seed))
    return p


@pytest.fixture(autouse=True)
def _no_registered_store():
    """Every test starts and ends with no durable store registered."""
    image_ledger.set_image_ledger_store(None)
    yield
    image_ledger.set_image_ledger_store(None)


class TestDedup:
    """(a) the same bytes twice in one session -> one block, then the marker."""

    def test_same_bytes_offered_twice_in_a_session_inline_once(self):
        first = apply_image_budget(
            [_text("a.png"), _image(1, "a.png", "/tmp/a.png")], empty_ledger()
        )
        assert len(_image_blocks(first.blocks)) == 1
        assert first.inlined == 1 and first.sent_earlier == 0
        assert first.ledger["hashes"] == [image_digest(_data(1))]

        second = apply_image_budget(
            [_text("a.png"), _image(1, "a.png", "/tmp/a.png")], first.ledger
        )
        assert _image_blocks(second.blocks) == []
        assert second.sent_earlier == 1 and second.inlined == 0
        assert second.blocks[0]["text"] == "look: " + SENT_EARLIER_MARKER.format(name="a.png")
        # Nothing new was sent, so the ledger is unchanged.
        assert second.ledger == first.ledger

    def test_the_key_is_content_not_name_or_path(self):
        ledger = apply_image_budget([_text("a.png"), _image(1, "a.png", "/one/a.png")], None).ledger
        again = apply_image_budget(
            [_text("copy.png"), _image(1, "copy.png", "/elsewhere/copy.png")], ledger
        )
        assert _image_blocks(again.blocks) == []
        assert again.blocks[0]["text"] == "look: " + SENT_EARLIER_MARKER.format(name="copy.png")

    def test_different_bytes_under_one_name_are_both_inlined(self):
        ledger = apply_image_budget([_text("shot.png"), _image(1, "shot.png")], None).ledger
        again = apply_image_budget([_text("shot.png"), _image(2, "shot.png")], ledger)
        assert len(_image_blocks(again.blocks)) == 1
        assert again.blocks[0]["text"] == "look: [image: shot.png]"
        assert len(again.ledger["hashes"]) == 2

    def test_the_same_bytes_twice_in_one_prompt_inline_once(self):
        result = apply_image_budget(
            [
                _text("a.png", "b.png"),
                _image(1, "a.png", "/t/a.png"),
                _image(1, "b.png", "/t/b.png"),
            ],
            None,
        )
        assert len(_image_blocks(result.blocks)) == 1
        assert result.blocks[0]["text"] == (
            "look: [image: a.png] " + SENT_EARLIER_MARKER.format(name="b.png")
        )
        assert result.sent_earlier == 1

    def test_dedup_wins_over_the_budget(self):
        """A repeat costs nothing, so it is reported as a repeat even at a full budget."""
        ledger = apply_image_budget([_text("a.png"), _image(1, "a.png")], None).ledger
        result = apply_image_budget(
            [_text("a.png"), _image(1, "a.png", "/t/a.png")], ledger, max_prompt_images=0
        )
        assert result.sent_earlier == 1 and result.over_budget == 0


class TestBudget:
    """(c) blocks up to the cap, markers after it, running total recorded."""

    def test_images_past_the_per_prompt_count_cap_become_markers(self):
        blocks = [_text("a.png", "b.png", "c.png")] + [
            _image(i, f"{n}.png", f"/t/{n}.png") for i, n in enumerate("abc", start=1)
        ]
        result = apply_image_budget(blocks, None, max_prompt_images=2)
        kept = _image_blocks(result.blocks)
        assert [b["data"] for b in kept] == [_data(1), _data(2)]
        assert result.inlined == 2 and result.over_budget == 1
        assert result.blocks[0]["text"] == "look: [image: a.png] [image: b.png] " + (
            OVER_BUDGET_MARKER.format(name="c.png", path="/t/c.png")
        )
        # The running total counts what was SENT, not what was offered.
        assert result.ledger["b64_bytes"] == len(_data(1)) + len(_data(2))
        assert result.ledger["hashes"] == [image_digest(_data(1)), image_digest(_data(2))]

    def test_the_per_prompt_byte_cap_is_inclusive(self):
        two = [_text("a.png", "b.png"), _image(1, "a.png"), _image(2, "b.png")]
        at_cap = apply_image_budget(two, None, max_prompt_b64_bytes=2 * len(_data(1)))
        assert at_cap.inlined == 2 and at_cap.over_budget == 0
        one_short = apply_image_budget(two, None, max_prompt_b64_bytes=2 * len(_data(1)) - 1)
        assert one_short.inlined == 1 and one_short.over_budget == 1

    def test_the_session_total_runs_across_prompts(self):
        size = len(_data(1))
        cap = 2 * size
        first = apply_image_budget(
            [_text("a.png"), _image(1, "a.png")], None, max_session_b64_bytes=cap
        )
        second = apply_image_budget(
            [_text("b.png"), _image(2, "b.png")], first.ledger, max_session_b64_bytes=cap
        )
        assert second.inlined == 1 and second.ledger["b64_bytes"] == cap
        third = apply_image_budget(
            [_text("c.png"), _image(3, "c.png", "/t/c.png")],
            second.ledger,
            max_session_b64_bytes=cap,
        )
        assert third.inlined == 0 and third.over_budget == 1
        assert third.blocks[0]["text"] == "look: " + OVER_BUDGET_MARKER.format(
            name="c.png", path="/t/c.png"
        )
        # A refused block was never sent: it neither counts nor becomes a known digest.
        assert third.ledger == second.ledger

    def test_a_refused_image_may_be_inlined_by_a_later_prompt(self):
        first = apply_image_budget(
            [_text("a.png", "b.png"), _image(1, "a.png"), _image(2, "b.png", "/t/b.png")],
            None,
            max_prompt_images=1,
        )
        assert first.over_budget == 1
        second = apply_image_budget(
            [_text("b.png"), _image(2, "b.png")], first.ledger, max_prompt_images=1
        )
        assert second.inlined == 1 and second.over_budget == 0

    def test_an_over_budget_marker_without_a_path_still_names_the_file(self):
        result = apply_image_budget([_text("a.png"), _image(1, "a.png")], None, max_prompt_images=0)
        assert (
            result.blocks[0]["text"] == "look: [image: a.png, not inlined: over the image budget]"
        )

    def test_an_unannotated_block_is_reported_with_a_generic_note(self):
        bare = {"type": "image", "data": _data(1), "mimeType": "image/png"}
        over = apply_image_budget(
            [{"type": "text", "text": "see"}, bare], None, max_prompt_images=0
        )
        assert _image_blocks(over.blocks) == []
        assert over.blocks[0]["text"] == "see\n" + UNNAMED_OVER_BUDGET_NOTE
        ledger = apply_image_budget([{"type": "text", "text": "see"}, bare], None).ledger
        repeat = apply_image_budget([{"type": "text", "text": "see"}, dict(bare)], ledger)
        assert repeat.blocks[0]["text"] == "see\n" + UNNAMED_SENT_EARLIER_NOTE

    def test_a_degraded_block_with_no_text_block_gets_one(self):
        result = apply_image_budget([_image(1)], None, max_prompt_images=0)
        assert result.blocks == [{"type": "text", "text": UNNAMED_OVER_BUDGET_NOTE}]

    def test_default_caps_are_shares_of_the_measured_ceiling(self):
        """The constants state their arithmetic; measure it through the code, then
        check the owning spec quotes the same figures rather than its own."""
        assert MAX_SESSION_IMAGE_B64_BYTES == 24 * MIB
        assert MAX_SESSION_IMAGE_B64_BYTES == (32 * MIB) * 3 // 4
        assert MAX_PROMPT_IMAGE_B64_BYTES == MAX_SESSION_IMAGE_B64_BYTES // 2 == 12 * MIB
        assert MAX_PROMPT_IMAGE_BLOCKS == 20
        assert MAX_PROMPT_IMAGE_B64_BYTES < MAX_SESSION_IMAGE_B64_BYTES < 32 * MIB
        text = SPEC.read_text(encoding="utf-8")
        start = text.index("**Per-session dedup and aggregate budget**")
        paragraph = text[start : text.index("\n\n", start)]
        for figure in (
            f"{MAX_SESSION_IMAGE_B64_BYTES // MIB} MiB per session",
            f"{MAX_PROMPT_IMAGE_B64_BYTES // MIB} MiB per prompt",
            f"{MAX_PROMPT_IMAGE_BLOCKS} image blocks per prompt",
            "32 MiB",
        ):
            assert figure in paragraph, figure
        # The ratio the spec states is the one the code computes.
        assert "three quarters" in paragraph and "half of that" in paragraph

    def test_default_caps_apply_when_no_override_is_given(self):
        many = [_text()] + [_image(i, size=4) for i in range(MAX_PROMPT_IMAGE_BLOCKS + 3)]
        result = apply_image_budget(many, None)
        assert result.inlined == MAX_PROMPT_IMAGE_BLOCKS and result.over_budget == 3


class TestWireShape:
    def test_host_side_annotations_never_reach_the_output(self):
        block = _image(1, "a.png", "/t/a.png")
        block["_other"] = "host only"
        result = apply_image_budget([_text("a.png"), block], None)
        (kept,) = _image_blocks(result.blocks)
        assert kept == {"type": "image", "data": _data(1), "mimeType": "image/png"}

    def test_inputs_are_not_mutated(self):
        text = _text("a.png", "b.png")
        blocks = [text, _image(1, "a.png"), _image(1, "b.png")]
        snapshot = json.dumps(blocks, sort_keys=True)
        ledger = empty_ledger()
        apply_image_budget(blocks, ledger)
        assert json.dumps(blocks, sort_keys=True) == snapshot
        assert ledger == empty_ledger()

    def test_a_kept_block_is_byte_identical(self):
        """(d) the layer neither re-encodes nor resizes: the per-image caps in
        ``imaging.py`` stay the only thing that touches the payload."""
        block = _image(7, "a.png")
        result = apply_image_budget([_text("a.png"), block], None)
        assert _image_blocks(result.blocks)[0]["data"] == block["data"]
        assert MAX_IMAGE_EDGE_PX == 2000
        assert MAX_IMAGE_B64_BYTES == 5 * MIB

    def test_blocks_of_other_shapes_pass_through_untouched(self):
        odd = [{"type": "tool_result", "x": 1}, "not a dict", {"type": "image", "data": 3}]
        result = apply_image_budget(list(odd), None)
        assert result.blocks == odd
        assert result.inlined == 0 and result.ledger == empty_ledger()

    def test_the_same_name_from_two_paths_rewrites_the_first_marker(self):
        """Two files sharing a basename are indistinguishable in the text once the
        builder has rewritten both to the same marker; the layer edits the first."""
        blocks = [
            _text("shot.png", "shot.png"),
            _image(1, "shot.png", "/a/shot.png"),
            _image(1, "shot.png", "/b/shot.png"),
        ]
        result = apply_image_budget(blocks, None)
        assert result.blocks[0]["text"] == (
            "look: " + SENT_EARLIER_MARKER.format(name="shot.png") + " [image: shot.png]"
        )


class TestLedgerNormalization:
    def test_malformed_records_read_as_empty(self):
        for raw in (None, "x", [], {"hashes": "abc", "b64_bytes": "9"}, {"b64_bytes": True}):
            assert normalize_ledger(raw) == empty_ledger(), raw

    def test_only_sha256_hex_digests_are_retained(self):
        good = image_digest("a")
        raw = {"hashes": [good, "short", 12, "x" * 65, None], "b64_bytes": -4}
        assert normalize_ledger(raw) == {"hashes": [good], "b64_bytes": 0}

    def test_the_digest_list_is_bounded_oldest_first(self):
        digests = [image_digest(str(i)) for i in range(MAX_LEDGER_HASHES + 5)]
        kept = normalize_ledger({"hashes": digests, "b64_bytes": 1})["hashes"]
        assert kept == digests[5:]
        assert len(kept) == MAX_LEDGER_HASHES

    def test_eviction_is_counted_and_only_the_newest_digests_are_kept(self):
        full = {"hashes": [image_digest(str(i)) for i in range(MAX_LEDGER_HASHES)], "b64_bytes": 0}
        result = apply_image_budget([_text("n.png"), _image(9, "n.png", size=4)], full)
        assert result.evicted == 1
        assert len(result.ledger["hashes"]) == MAX_LEDGER_HASHES
        assert result.ledger["hashes"][-1] == image_digest(_data(9, 4))
        assert result.ledger["hashes"][0] == image_digest("1")


@pytest.fixture()
def patched_map(tmp_path, monkeypatch):
    kiro = tmp_path / "kiro"
    kiro.mkdir()
    monkeypatch.setattr("kiro_crew.session_map.config_dir", lambda: tmp_path)
    monkeypatch.setattr("kiro_crew.session_map._KIRO_SESSIONS_DIR", kiro)
    return tmp_path


class TestSessionRecord:
    """(b) the ledger is on the session record and survives a restart."""

    def test_a_key_with_no_entry_takes_no_ledger_and_gains_no_entry(self, patched_map):
        sm = SessionMap()
        assert sm.get_image_ledger("dashboard:1") is None
        assert (
            sm.set_image_ledger("dashboard:1", {"hashes": [image_digest("a")], "b64_bytes": 3})
            is False
        )
        assert sm.get_image_ledger("dashboard:1") is None
        assert not (patched_map / "session_map.json").exists(), "nothing was written"

    def test_the_ledger_survives_a_reload(self, patched_map):
        sm = SessionMap()
        sm.set("dashboard:1", "sid-1")
        assert sm.get_image_ledger("dashboard:1") == {}
        ledger = {"hashes": [image_digest("a"), image_digest("b")], "b64_bytes": 4321}
        assert sm.set_image_ledger("dashboard:1", ledger) is True
        # A fresh instance reads the file: that is what a gateway restart does.
        assert SessionMap().get_image_ledger("dashboard:1") == ledger
        assert SessionMap().mapped_sid("dashboard:1") == "sid-1", "the sid is untouched"

    def test_a_new_native_conversation_starts_an_empty_ledger(self, patched_map):
        sm = SessionMap()
        sm.set("dashboard:1", "sid-1")
        sm.set_image_ledger("dashboard:1", {"hashes": [image_digest("a")], "b64_bytes": 10})
        sm.set("dashboard:1", "sid-1", cwd="/somewhere")
        assert sm.get_image_ledger("dashboard:1")["b64_bytes"] == 10, "same sid keeps it"
        sm.set("dashboard:1", "sid-2")
        assert sm.get_image_ledger("dashboard:1") == {}
        assert SessionMap().get_image_ledger("dashboard:1") == {}

    def test_a_cleared_sid_then_a_fresh_one_drops_the_ledger(self, patched_map):
        sm = SessionMap()
        sm.set("dashboard:1", "sid-1")
        sm.set_image_ledger("dashboard:1", {"hashes": [image_digest("a")], "b64_bytes": 10})
        assert sm.clear_sid("dashboard:1") is True
        sm.set("dashboard:1", "sid-3")
        assert sm.get_image_ledger("dashboard:1") == {}

    def test_an_empty_ledger_leaves_no_field_behind(self, patched_map):
        sm = SessionMap()
        sm.set("dashboard:1", "sid-1")
        sm.set_image_ledger("dashboard:1", {"hashes": [image_digest("a")], "b64_bytes": 10})
        sm.set_image_ledger("dashboard:1", empty_ledger())
        raw = json.loads((patched_map / "session_map.json").read_text(encoding="utf-8"))
        assert "image_ledger" not in raw["dashboard:1"]

    def test_the_record_is_normalized_at_the_point_of_retention(self, patched_map):
        sm = SessionMap()
        sm.set("dashboard:1", "sid-1")
        digests = [image_digest(str(i)) for i in range(MAX_LEDGER_HASHES + 2)]
        sm.set_image_ledger("dashboard:1", {"hashes": digests + ["junk"], "b64_bytes": -1})
        stored = SessionMap().get_image_ledger("dashboard:1")
        assert stored["hashes"] == digests[2:] and stored["b64_bytes"] == 0

    @pytest.mark.asyncio
    async def test_the_layer_dedups_across_a_simulated_gateway_restart(self, patched_map):
        """The whole path: layer -> registered live map -> disk -> new process."""
        first_map = SessionMap()
        first_map.set("dashboard:7", "sid-7")
        image_ledger.set_image_ledger_store(first_map)
        budget = SessionImageBudget(lambda: "dashboard:7")

        sent = await budget.apply([_text("a.png"), _image(1, "a.png", "/t/a.png")])
        assert len(_image_blocks(sent)) == 1
        # The deferred flush lands (and its task retires) before the "restart".
        await first_map.aclose()

        # Restart: a new map read from disk, a new handle, a new applier.
        second_map = SessionMap()
        image_ledger.set_image_ledger_store(second_map)
        again = await SessionImageBudget(lambda: "dashboard:7").apply(
            [_text("a.png"), _image(1, "a.png", "/t/a.png")]
        )
        assert _image_blocks(again) == []
        assert again[0]["text"] == "look: " + SENT_EARLIER_MARKER.format(name="a.png")
        await second_map.aclose()

    @pytest.mark.asyncio
    async def test_a_session_without_a_record_keeps_an_in_memory_ledger(self, patched_map):
        sm = SessionMap()
        image_ledger.set_image_ledger_store(sm)
        budget = SessionImageBudget(lambda: "subagent:x")
        assert len(_image_blocks(await budget.apply([_text("a.png"), _image(1, "a.png")]))) == 1
        again = await budget.apply([_text("a.png"), _image(1, "a.png")])
        assert _image_blocks(again) == []
        assert sm.get_image_ledger("subagent:x") is None, "no entry was materialized"
        await sm.aclose()
        assert not (patched_map / "session_map.json").exists(), "nothing was written"

    @pytest.mark.asyncio
    async def test_no_store_registered_still_dedups_in_memory(self):
        budget = SessionImageBudget(lambda: "dashboard:1")
        assert len(_image_blocks(await budget.apply([_text("a.png"), _image(1, "a.png")]))) == 1
        assert _image_blocks(await budget.apply([_text("a.png"), _image(1, "a.png")])) == []

    @pytest.mark.asyncio
    async def test_text_only_prompts_are_returned_as_is(self):
        blocks = [{"type": "text", "text": "hi"}]
        assert await SessionImageBudget(lambda: "k").apply(blocks) is blocks


class TestBuilderAnnotation:
    def test_the_builder_annotates_each_image_with_its_source(self, tmp_path):
        p = _png(tmp_path, "shot.png")
        blocks = build_prompt_blocks(f"see {p}")
        assert blocks[0]["text"] == "see [image: shot.png]"
        assert blocks[1][IMAGE_BLOCK_SOURCE_KEY] == {"name": "shot.png", "path": str(p)}

    def test_the_layer_rewrites_the_marker_the_builder_wrote(self, tmp_path):
        p = _png(tmp_path, "shot.png")
        first = apply_image_budget(build_prompt_blocks(f"see {p}"), None)
        second = apply_image_budget(build_prompt_blocks(f"again {p}"), first.ledger)
        assert _image_blocks(second.blocks) == []
        assert second.blocks[0]["text"] == "again " + SENT_EARLIER_MARKER.format(name="shot.png")
        over = apply_image_budget(build_prompt_blocks(f"see {p}"), None, max_prompt_images=0)
        assert over.blocks[0]["text"] == "see " + OVER_BUDGET_MARKER.format(
            name="shot.png", path=str(p)
        )


def _handle(tmp_path: Path, sent: list[dict]) -> AcpSessionHandle:
    """A handle on a fake runtime that records the prompt params and ends the turn."""
    runtime = AcpRuntime(work_dir=str(tmp_path))
    runtime._initialized = True
    runtime._prompt_capabilities = {"image": True}
    queue: asyncio.Queue = asyncio.Queue()
    runtime._session_queues["s1"] = queue

    async def send_request(method, params):
        sent.append(params)
        raise AcpRuntimeDead("turn ends here")

    runtime.send_request = send_request
    return AcpSessionHandle("s1", queue, runtime, session_key="dashboard:1")


async def _prompt(handle: AcpSessionHandle, message: str) -> None:
    gen = handle.prompt(message, timeout=1.0)
    with pytest.raises(AcpRuntimeDead):
        await gen.__anext__()
    await gen.aclose()


class TestHandleWiring:
    @pytest.mark.asyncio
    async def test_the_handle_applies_the_layer_across_turns(self, tmp_path):
        p = _png(tmp_path, "shot.png")
        sent: list[dict] = []
        handle = _handle(tmp_path, sent)
        await _prompt(handle, f"first {p}")
        await _prompt(handle, f"second {p}")
        assert [b["type"] for b in sent[0]["prompt"]] == ["text", "image"]
        assert [b["type"] for b in sent[1]["prompt"]] == ["text"]
        assert sent[1]["prompt"][0]["text"] == "second " + SENT_EARLIER_MARKER.format(
            name="shot.png"
        )

    @pytest.mark.asyncio
    async def test_nothing_host_side_reaches_the_wire(self, tmp_path):
        p = _png(tmp_path, "shot.png")
        sent: list[dict] = []
        await _prompt(_handle(tmp_path, sent), f"see {p}")
        for block in sent[0]["prompt"]:
            assert not any(str(k).startswith("_") for k in block), block.keys()

    @pytest.mark.asyncio
    async def test_the_handle_uses_the_durable_record_when_the_session_has_one(
        self, tmp_path, patched_map
    ):
        sm = SessionMap()
        sm.set("dashboard:1", "sid-1")
        image_ledger.set_image_ledger_store(sm)
        p = _png(tmp_path, "shot.png")
        sent: list[dict] = []
        await _prompt(_handle(tmp_path, sent), f"see {p}")
        digest = image_digest(sent[0]["prompt"][1]["data"])
        assert sm.get_image_ledger("dashboard:1")["hashes"] == [digest]
        # A NEW handle (a recycled session) reads the same record and dedups.
        await _prompt(_handle(tmp_path, sent), f"again {p}")
        assert [b["type"] for b in sent[1]["prompt"]] == ["text"]
        await sm.aclose()

    @pytest.mark.asyncio
    async def test_the_direct_client_applies_the_same_layer(self, tmp_path):
        p = _png(tmp_path, "shot.png")
        client = AcpClient(work_dir=tmp_path, session_key="dashboard:9")
        client._session_id = "s9"
        sent: list[dict] = []

        async def send_request(method, params):
            sent.append(params)
            return 1

        client._send_request = send_request
        await client._send_prompt(f"one {p}")
        await client._send_prompt(f"two {p}")
        assert [b["type"] for b in sent[0]["prompt"]] == ["text", "image"]
        assert not any(str(k).startswith("_") for k in sent[0]["prompt"][1])
        assert [b["type"] for b in sent[1]["prompt"]] == ["text"]


class TestSpecAndConftest:
    def test_the_layer_is_listed_in_the_spec_beside_the_per_image_caps(self):
        text = SPEC.read_text(encoding="utf-8")
        assert "image_ledger.py" in text and "SessionImageBudget" in text

    def test_the_marker_shapes_are_stable_strings(self):
        assert SENT_EARLIER_MARKER.format(name="x.png") == "[image: x.png, sent earlier]"
        assert re.fullmatch(
            r"\[image: x\.png, not inlined: over the image budget; file: /p/x\.png\]",
            (OVER_BUDGET_MARKER.format(name="x.png", path="/p/x.png")),
        )
