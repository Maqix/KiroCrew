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
layer after the builder, charge the ledger only once the prompt frame is
written, and let no host-side annotation reach the wire. The ledger names the
native conversation (``sid``) it describes, so a fresh conversation -- even one
whose sid promotion is deferred behind a history replay -- never reads the
previous one's ledger, and a confirmed native ``/clear`` empties it.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
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
from kiro_crew.acp.types import METHOD_CLEAR_STATUS, JsonRpcMessage
from kiro_crew.image_ledger import (
    IMAGE_BLOCK_SOURCE_KEY,
    MAX_LEDGER_HASHES,
    MAX_PROMPT_IMAGE_B64_BYTES,
    MAX_PROMPT_IMAGE_BLOCKS,
    MAX_SESSION_IMAGE_B64_BYTES,
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
SID = "sid-1"


def _data(seed: int, size: int = 120) -> str:
    """A deterministic base64 payload of exactly ``size`` characters, distinct per seed."""
    raw = bytes([seed % 256]) * (size * 3 // 4)
    out = base64.b64encode(raw).decode("ascii")
    assert len(out) == size, (len(out), size)
    return out


def _sent(name: str, path: str = "") -> str:
    """The literal the layer writes for a repeat (with or without a path)."""
    return (
        f"[image: {name}, sent earlier; file: {path}]" if path else f"[image: {name}, sent earlier]"
    )


def _over(name: str, path: str = "") -> str:
    """The literal the layer writes for a block over the budget."""
    tail = f"; file: {path}]" if path else "]"
    return f"[image: {name}, not inlined: over the image budget{tail}"


def _prompt(*specs: tuple, lead: str = "look: ", size: int = 120) -> list[dict]:
    """Blocks the builder would produce for ``lead`` + one marker per spec.

    Each spec is ``(seed, name, path)``; ``path`` may be ``""``. The text
    carries ``[image: <name>]`` per spec separated by spaces, and every image
    block is annotated with its marker's exact offsets, as the builder does.
    """
    text = lead
    images: list[dict] = []
    for seed, name, path in specs:
        marker = f"[image: {name}]"
        if text != lead:
            text += " "
        start = len(text)
        text += marker
        images.append(
            {
                "type": "image",
                "data": _data(seed, size),
                "mimeType": "image/png",
                IMAGE_BLOCK_SOURCE_KEY: {"path": path, "spans": [[start, len(text)]]},
            }
        )
    return [{"type": "text", "text": text}, *images]


def _bare_image(seed: int, size: int = 120) -> dict:
    """An image block with no annotation at all."""
    return {"type": "image", "data": _data(seed, size), "mimeType": "image/png"}


def _image_blocks(blocks: list[dict]) -> list[dict]:
    return [b for b in blocks if b.get("type") == "image"]


def _png_bytes(seed: int) -> bytes:
    pil = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    pil.new("RGB", (4, 4), (seed % 256, 90, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _png(tmp_path: Path, name: str, seed: int = 1) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / name
    p.write_bytes(_png_bytes(seed))
    return p


def _budget(key: str, sid: str = SID) -> SessionImageBudget:
    return SessionImageBudget(lambda: key, lambda: sid)


@pytest.fixture(autouse=True)
def _no_registered_store():
    """Every test starts and ends with no durable store registered."""
    image_ledger.set_image_ledger_store(None)
    yield
    image_ledger.set_image_ledger_store(None)


@pytest.fixture
def caps(monkeypatch):
    """Set the module's caps small for one test (the constants are read at call time)."""

    def _set(
        *,
        prompt_images: int | None = None,
        prompt_b64: int | None = None,
        session_b64: int | None = None,
    ):
        if prompt_images is not None:
            monkeypatch.setattr(image_ledger, "MAX_PROMPT_IMAGE_BLOCKS", prompt_images)
        if prompt_b64 is not None:
            monkeypatch.setattr(image_ledger, "MAX_PROMPT_IMAGE_B64_BYTES", prompt_b64)
        if session_b64 is not None:
            monkeypatch.setattr(image_ledger, "MAX_SESSION_IMAGE_B64_BYTES", session_b64)

    return _set


class TestDedup:
    """(a) the same bytes twice in one session -> one block, then the marker."""

    def test_same_bytes_offered_twice_in_a_session_inline_once(self):
        first = apply_image_budget(_prompt((1, "a.png", "/tmp/a.png")), empty_ledger(SID))
        assert len(_image_blocks(first.blocks)) == 1
        assert first.inlined == 1 and first.sent_earlier == 0
        assert first.ledger["hashes"] == [image_digest(_data(1))]
        assert first.ledger["sid"] == SID

        second = apply_image_budget(_prompt((1, "a.png", "/tmp/a.png")), first.ledger)
        assert _image_blocks(second.blocks) == []
        assert second.sent_earlier == 1 and second.inlined == 0
        assert second.blocks[0]["text"] == "look: " + _sent("a.png", "/tmp/a.png")
        # Nothing new was sent, so the ledger is unchanged.
        assert second.ledger == first.ledger

    def test_the_key_is_content_not_name_or_path(self):
        ledger = apply_image_budget(_prompt((1, "a.png", "/one/a.png")), None).ledger
        again = apply_image_budget(_prompt((1, "copy.png", "/elsewhere/copy.png")), ledger)
        assert _image_blocks(again.blocks) == []
        assert again.blocks[0]["text"] == "look: " + _sent("copy.png", "/elsewhere/copy.png")

    def test_different_bytes_under_one_name_are_both_inlined(self):
        ledger = apply_image_budget(_prompt((1, "shot.png", "")), None).ledger
        again = apply_image_budget(_prompt((2, "shot.png", "")), ledger)
        assert len(_image_blocks(again.blocks)) == 1
        assert again.blocks[0]["text"] == "look: [image: shot.png]"
        assert len(again.ledger["hashes"]) == 2

    def test_the_same_bytes_twice_in_one_prompt_inline_once(self):
        result = apply_image_budget(
            _prompt((1, "a.png", "/t/a.png"), (1, "b.png", "/t/b.png")), None
        )
        assert len(_image_blocks(result.blocks)) == 1
        assert result.blocks[0]["text"] == "look: [image: a.png] " + _sent("b.png", "/t/b.png")
        assert result.sent_earlier == 1

    def test_dedup_wins_over_the_budget(self, caps):
        """A repeat costs nothing, so it is reported as a repeat even at a full budget."""
        ledger = apply_image_budget(_prompt((1, "a.png", "")), None).ledger
        caps(prompt_images=0)
        result = apply_image_budget(_prompt((1, "a.png", "/t/a.png")), ledger)
        assert result.sent_earlier == 1 and result.over_budget == 0

    def test_a_repeat_without_a_path_reads_sent_earlier_alone(self):
        ledger = apply_image_budget(_prompt((1, "a.png", "")), None).ledger
        again = apply_image_budget(_prompt((1, "a.png", "")), ledger)
        assert again.blocks[0]["text"] == "look: " + _sent("a.png")


class TestBudget:
    """(c) blocks up to the cap, markers after it, running total recorded."""

    def test_images_past_the_per_prompt_count_cap_become_markers(self, caps):
        blocks = _prompt(*[(i, f"{n}.png", f"/t/{n}.png") for i, n in enumerate("abc", start=1)])
        caps(prompt_images=2)
        result = apply_image_budget(blocks, None)
        kept = _image_blocks(result.blocks)
        assert [b["data"] for b in kept] == [_data(1), _data(2)]
        assert result.inlined == 2 and result.over_budget == 1
        assert result.blocks[0]["text"] == "look: [image: a.png] [image: b.png] " + _over(
            "c.png", "/t/c.png"
        )
        # The running total counts what was SENT, not what was offered.
        assert result.ledger["b64_bytes"] == len(_data(1)) + len(_data(2))
        assert result.ledger["hashes"] == [image_digest(_data(1)), image_digest(_data(2))]

    def test_the_per_prompt_byte_cap_is_inclusive(self, caps):
        two = _prompt((1, "a.png", ""), (2, "b.png", ""))
        caps(prompt_b64=2 * len(_data(1)))
        at_cap = apply_image_budget(two, None)
        assert at_cap.inlined == 2 and at_cap.over_budget == 0
        caps(prompt_b64=2 * len(_data(1)) - 1)
        one_short = apply_image_budget(two, None)
        assert one_short.inlined == 1 and one_short.over_budget == 1

    def test_the_session_total_runs_across_prompts(self, caps):
        size = len(_data(1))
        cap = 2 * size
        caps(session_b64=cap)
        first = apply_image_budget(_prompt((1, "a.png", "")), None)
        second = apply_image_budget(_prompt((2, "b.png", "")), first.ledger)
        assert second.inlined == 1 and second.ledger["b64_bytes"] == cap
        third = apply_image_budget(_prompt((3, "c.png", "/t/c.png")), second.ledger)
        assert third.inlined == 0 and third.over_budget == 1
        assert third.blocks[0]["text"] == "look: " + _over("c.png", "/t/c.png")
        # A refused block was never sent: it neither counts nor becomes a known digest.
        assert third.ledger == second.ledger

    def test_a_refused_image_may_be_inlined_by_a_later_prompt(self, caps):
        caps(prompt_images=1)
        first = apply_image_budget(_prompt((1, "a.png", ""), (2, "b.png", "/t/b.png")), None)
        assert first.over_budget == 1
        second = apply_image_budget(_prompt((2, "b.png", "")), first.ledger)
        assert second.inlined == 1 and second.over_budget == 0

    def test_an_over_budget_marker_without_a_path_still_names_the_file(self, caps):
        caps(prompt_images=0)
        result = apply_image_budget(_prompt((1, "a.png", "")), None)
        assert result.blocks[0]["text"] == "look: " + _over("a.png")

    def test_an_unannotated_block_is_reported_with_a_generic_note(self, monkeypatch):
        monkeypatch.setattr(image_ledger, "MAX_PROMPT_IMAGE_BLOCKS", 0)
        over = apply_image_budget([{"type": "text", "text": "see"}, _bare_image(1)], None)
        assert _image_blocks(over.blocks) == []
        assert over.blocks[0]["text"] == "see\n" + UNNAMED_OVER_BUDGET_NOTE
        monkeypatch.setattr(image_ledger, "MAX_PROMPT_IMAGE_BLOCKS", MAX_PROMPT_IMAGE_BLOCKS)
        ledger = apply_image_budget([{"type": "text", "text": "see"}, _bare_image(1)], None).ledger
        repeat = apply_image_budget([{"type": "text", "text": "see"}, _bare_image(1)], ledger)
        assert repeat.blocks[0]["text"] == "see\n" + UNNAMED_SENT_EARLIER_NOTE

    def test_a_degraded_block_with_no_text_block_gets_one(self, caps):
        caps(prompt_images=0)
        result = apply_image_budget([_bare_image(1)], None)
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

    def test_the_default_caps_apply(self):
        many = [{"type": "text", "text": "x"}] + [
            _bare_image(i, size=4) for i in range(MAX_PROMPT_IMAGE_BLOCKS + 3)
        ]
        result = apply_image_budget(many, None)
        assert result.inlined == MAX_PROMPT_IMAGE_BLOCKS and result.over_budget == 3


class TestWireShape:
    def test_host_side_annotations_never_reach_the_output(self):
        blocks = _prompt((1, "a.png", "/t/a.png"))
        blocks[1]["_other"] = "host only"
        result = apply_image_budget(blocks, None)
        (kept,) = _image_blocks(result.blocks)
        assert kept == {"type": "image", "data": _data(1), "mimeType": "image/png"}

    def test_inputs_are_not_mutated(self):
        blocks = _prompt((1, "a.png", ""), (1, "b.png", ""))
        snapshot = json.dumps(blocks, sort_keys=True)
        ledger = empty_ledger(SID)
        apply_image_budget(blocks, ledger)
        assert json.dumps(blocks, sort_keys=True) == snapshot
        assert ledger == empty_ledger(SID)

    def test_a_kept_block_is_byte_identical(self):
        """(d) the layer neither re-encodes nor resizes: the per-image caps in
        ``imaging.py`` stay the only thing that touches the payload."""
        blocks = _prompt((7, "a.png", ""))
        result = apply_image_budget(blocks, None)
        assert _image_blocks(result.blocks)[0]["data"] == blocks[1]["data"]
        assert MAX_IMAGE_EDGE_PX == 2000
        assert MAX_IMAGE_B64_BYTES == 5 * MIB

    def test_blocks_of_other_shapes_pass_through_untouched(self):
        odd = [{"type": "tool_result", "x": 1}, "not a dict", {"type": "image", "data": 3}]
        result = apply_image_budget(list(odd), None)
        assert result.blocks == odd
        assert result.inlined == 0 and result.ledger == empty_ledger()


class TestMarkerIdentity:
    """A dropped block rewrites the characters its producer substituted, nothing else."""

    def test_two_files_sharing_a_basename_get_distinct_markers(self, tmp_path):
        a = _png(tmp_path / "a", "shot.png", seed=1)
        b = _png(tmp_path / "b", "shot.png", seed=2)
        blocks = build_prompt_blocks(f"first {a} then {b}")
        text = blocks[0]["text"]
        assert text == "first [image: shot.png] then [image: shot.png (2)]"
        ((s1, e1),) = blocks[1][IMAGE_BLOCK_SOURCE_KEY]["spans"]
        ((s2, e2),) = blocks[2][IMAGE_BLOCK_SOURCE_KEY]["spans"]
        assert text[s1:e1] == "[image: shot.png]" and text[s2:e2] == "[image: shot.png (2)]"

    def test_the_dropped_block_rewrites_its_own_marker_not_a_neighbours(
        self, tmp_path, monkeypatch
    ):
        a = _png(tmp_path / "a", "shot.png", seed=1)
        b = _png(tmp_path / "b", "shot.png", seed=2)
        monkeypatch.setattr(image_ledger, "MAX_PROMPT_IMAGE_BLOCKS", 1)
        over = apply_image_budget(build_prompt_blocks(f"first {a} then {b}"), None)
        assert [x["data"] for x in _image_blocks(over.blocks)] == [
            base64.b64encode(_png_bytes(1)).decode("ascii")
        ]
        assert over.blocks[0]["text"] == "first [image: shot.png] then " + _over(
            "shot.png (2)", str(b)
        )
        # The same payload under two names: the SECOND block is the repeat.
        c = _png(tmp_path / "c", "shot.png", seed=1)
        dup = apply_image_budget(build_prompt_blocks(f"first {a} then {c}"), None)
        assert dup.blocks[0]["text"] == "first [image: shot.png] then " + _sent(
            "shot.png (2)", str(c)
        )

    def test_every_place_the_dropped_file_was_named_is_rewritten(self, tmp_path):
        p = _png(tmp_path, "shot.png")
        ledger = apply_image_budget(build_prompt_blocks(f"see {p}"), None).ledger
        blocks = build_prompt_blocks(f"{p} and once more {p}")
        assert len(blocks[1][IMAGE_BLOCK_SOURCE_KEY]["spans"]) == 2
        again = apply_image_budget(blocks, ledger)
        marker = _sent("shot.png", str(p))
        assert again.blocks[0]["text"] == f"{marker} and once more {marker}"

    def test_a_bracketed_string_the_user_typed_is_never_rewritten(self, tmp_path, monkeypatch):
        """The user quotes ``[image: shot.png]`` literally while attaching that very
        file over budget: only the builder's substitution is rewritten, the quoted
        text is left as the user wrote it."""
        p = _png(tmp_path, "shot.png")
        blocks = build_prompt_blocks(f"you said [image: shot.png] earlier; here it is: {p}")
        monkeypatch.setattr(image_ledger, "MAX_PROMPT_IMAGE_BLOCKS", 0)
        over = apply_image_budget(blocks, None)
        assert over.blocks[0]["text"] == (
            "you said [image: shot.png] earlier; here it is: " + _over("shot.png", str(p))
        )
        # The repeat route lands on the same substitution and nothing else.
        monkeypatch.setattr(image_ledger, "MAX_PROMPT_IMAGE_BLOCKS", MAX_PROMPT_IMAGE_BLOCKS)
        ledger = apply_image_budget(build_prompt_blocks(f"see {p}"), None).ledger
        dup = apply_image_budget(build_prompt_blocks(f"[image: shot.png] again {p}"), ledger)
        assert dup.blocks[0]["text"] == "[image: shot.png] again " + _sent("shot.png", str(p))

    def test_only_the_grammars_own_matches_become_marker_sites(self, tmp_path):
        """The substitution follows the grammar's matches: the same characters glued
        to the tail of another token are not a candidate, so they stay as written
        even when the path stands alone elsewhere in the message."""
        p = _png(tmp_path, "shot.png")
        blocks = build_prompt_blocks(f"token{p} is not a path but {p} is")
        assert blocks[0]["text"] == f"token{p} is not a path but [image: shot.png] is"
        assert len(blocks[1][IMAGE_BLOCK_SOURCE_KEY]["spans"]) == 1


class TestLedgerNormalization:
    def test_malformed_records_read_as_empty(self):
        for raw in (None, "x", [], {"hashes": "abc", "b64_bytes": "9"}, {"b64_bytes": True}):
            assert normalize_ledger(raw) == empty_ledger(), raw

    def test_only_lowercase_hex_sha256_digests_are_retained(self):
        good = image_digest("a")
        raw = {
            "hashes": [good, "short", 12, "x" * 65, None, "g" * 64, good.upper(), "0" * 63 + "-"],
            "b64_bytes": -4,
        }
        assert normalize_ledger(raw) == {"sid": "", "hashes": [good], "b64_bytes": 0}

    def test_the_sid_is_kept_when_it_is_a_string(self):
        assert normalize_ledger({"sid": "abc"})["sid"] == "abc"
        assert normalize_ledger({"sid": 7})["sid"] == ""

    def test_the_digest_list_is_bounded_oldest_first(self):
        digests = [image_digest(str(i)) for i in range(MAX_LEDGER_HASHES + 5)]
        kept = normalize_ledger({"hashes": digests, "b64_bytes": 1})["hashes"]
        assert kept == digests[5:]
        assert len(kept) == MAX_LEDGER_HASHES

    def test_eviction_is_counted_and_only_the_newest_digests_are_kept(self):
        full = {
            "sid": SID,
            "hashes": [image_digest(str(i)) for i in range(MAX_LEDGER_HASHES)],
            "b64_bytes": 0,
        }
        result = apply_image_budget(_prompt((9, "n.png", ""), size=4), full)
        assert result.evicted == 1
        assert len(result.ledger["hashes"]) == MAX_LEDGER_HASHES
        assert result.ledger["hashes"][-1] == image_digest(_data(9, 4))
        assert result.ledger["hashes"][0] == image_digest("1")
        assert result.ledger["sid"] == SID


@pytest.fixture()
def patched_map(tmp_path, monkeypatch):
    kiro = tmp_path / "kiro"
    kiro.mkdir()
    monkeypatch.setattr("kiro_crew.session_map.config_dir", lambda: tmp_path)
    monkeypatch.setattr("kiro_crew.session_map._KIRO_SESSIONS_DIR", kiro)
    return tmp_path


def _ledger(*digests: str, sid: str = SID, b64_bytes: int = 10) -> dict:
    return {"sid": sid, "hashes": list(digests), "b64_bytes": b64_bytes}


class TestSessionRecord:
    """(b) the ledger is on the session record and survives a restart."""

    def test_a_key_with_no_entry_takes_no_ledger_and_gains_no_entry(self, patched_map):
        sm = SessionMap()
        assert sm.get_image_ledger("dashboard:1") is None
        assert sm.set_image_ledger("dashboard:1", _ledger(image_digest("a"))) is False
        assert sm.get_image_ledger("dashboard:1") is None
        assert not (patched_map / "session_map.json").exists(), "nothing was written"

    def test_the_ledger_survives_a_reload(self, patched_map):
        sm = SessionMap()
        sm.set("dashboard:1", SID)
        assert sm.get_image_ledger("dashboard:1") == {}
        ledger = _ledger(image_digest("a"), image_digest("b"), b64_bytes=4321)
        assert sm.set_image_ledger("dashboard:1", ledger) is True
        # A fresh instance reads the file: that is what a gateway restart does.
        assert SessionMap().get_image_ledger("dashboard:1") == ledger
        assert SessionMap().mapped_sid("dashboard:1") == SID, "the sid is untouched"

    def test_a_new_native_conversation_starts_an_empty_ledger(self, patched_map):
        sm = SessionMap()
        sm.set("dashboard:1", SID)
        sm.set_image_ledger("dashboard:1", _ledger(image_digest("a")))
        sm.set("dashboard:1", SID, cwd="/somewhere")
        assert sm.get_image_ledger("dashboard:1")["b64_bytes"] == 10, "same sid keeps it"
        sm.set("dashboard:1", "sid-2")
        assert sm.get_image_ledger("dashboard:1") == {}
        assert SessionMap().get_image_ledger("dashboard:1") == {}

    def test_a_cleared_sid_then_a_fresh_one_drops_the_ledger(self, patched_map):
        sm = SessionMap()
        sm.set("dashboard:1", SID)
        sm.set_image_ledger("dashboard:1", _ledger(image_digest("a")))
        assert sm.clear_sid("dashboard:1") is True
        sm.set("dashboard:1", "sid-3")
        assert sm.get_image_ledger("dashboard:1") == {}

    def test_a_ledger_written_under_a_deferred_sid_survives_its_promotion(self, patched_map):
        """A fresh session behind a history replay writes its ledger under the NEW
        sid while the entry still records the old one; recording the new sid
        must keep that ledger, and the old conversation's ledger must not have
        been readable by the new one in the first place."""
        sm = SessionMap()
        sm.set("dashboard:1", "old-sid")
        sm.set_image_ledger("dashboard:1", _ledger(image_digest("old"), sid="old-sid"))
        image_ledger.set_image_ledger_store(sm)
        assert image_ledger.load_image_ledger("dashboard:1", "new-sid") == empty_ledger("new-sid")
        assert image_ledger.load_image_ledger("dashboard:1", "old-sid")["hashes"] == [
            image_digest("old")
        ]
        # The fresh conversation's first turn writes its own ledger...
        sm.set_image_ledger("dashboard:1", _ledger(image_digest("new"), sid="new-sid"))
        # ...and the promotion that follows the landed turn keeps it.
        sm.set("dashboard:1", "new-sid")
        assert sm.get_image_ledger("dashboard:1")["hashes"] == [image_digest("new")]
        sm.set("dashboard:1", "other-sid")
        assert sm.get_image_ledger("dashboard:1") == {}

    def test_an_empty_ledger_leaves_no_field_behind(self, patched_map):
        sm = SessionMap()
        sm.set("dashboard:1", SID)
        sm.set_image_ledger("dashboard:1", _ledger(image_digest("a")))
        sm.set_image_ledger("dashboard:1", empty_ledger(SID))
        raw = json.loads((patched_map / "session_map.json").read_text(encoding="utf-8"))
        assert "image_ledger" not in raw["dashboard:1"]

    def test_the_record_is_normalized_at_the_point_of_retention(self, patched_map):
        sm = SessionMap()
        sm.set("dashboard:1", SID)
        digests = [image_digest(str(i)) for i in range(MAX_LEDGER_HASHES + 2)]
        sm.set_image_ledger(
            "dashboard:1", {"sid": "s" * 1000, "hashes": digests + ["junk"], "b64_bytes": -1}
        )
        stored = SessionMap().get_image_ledger("dashboard:1")
        assert stored["hashes"] == digests[2:] and stored["b64_bytes"] == 0
        assert stored["sid"] == "", "an over-long sid is refused, not truncated"

    @pytest.mark.asyncio
    async def test_the_layer_dedups_across_a_simulated_gateway_restart(self, patched_map):
        """The whole path: layer -> registered live map -> disk -> new process."""
        first_map = SessionMap()
        first_map.set("dashboard:7", SID)
        image_ledger.set_image_ledger_store(first_map)
        budget = _budget("dashboard:7")

        sent = await budget.apply(_prompt((1, "a.png", "/t/a.png")))
        assert len(_image_blocks(sent)) == 1
        budget.commit()  # the prompt was written
        # The deferred flush lands (and its task retires) before the "restart".
        await first_map.aclose()

        # Restart: a new map read from disk, a new handle, a new applier.
        second_map = SessionMap()
        image_ledger.set_image_ledger_store(second_map)
        again = await _budget("dashboard:7").apply(_prompt((1, "a.png", "/t/a.png")))
        assert _image_blocks(again) == []
        assert again[0]["text"] == "look: " + _sent("a.png", "/t/a.png")
        await second_map.aclose()

    @pytest.mark.asyncio
    async def test_a_session_without_a_record_keeps_an_in_memory_ledger(self, patched_map):
        sm = SessionMap()
        image_ledger.set_image_ledger_store(sm)
        budget = _budget("subagent:x")
        assert len(_image_blocks(await budget.apply(_prompt((1, "a.png", ""))))) == 1
        budget.commit()
        again = await budget.apply(_prompt((1, "a.png", "")))
        assert _image_blocks(again) == []
        assert sm.get_image_ledger("subagent:x") is None, "no entry was materialized"
        await sm.aclose()
        assert not (patched_map / "session_map.json").exists(), "nothing was written"

    @pytest.mark.asyncio
    async def test_an_in_memory_ledger_follows_its_owners_native_conversation(self):
        sid = {"value": "one"}
        budget = SessionImageBudget(lambda: "subagent:x", lambda: sid["value"])
        await budget.apply(_prompt((1, "a.png", "")))
        budget.commit()
        assert _image_blocks(await budget.apply(_prompt((1, "a.png", "")))) == []
        sid["value"] = "two"  # the owner reset onto a fresh native conversation
        assert len(_image_blocks(await budget.apply(_prompt((1, "a.png", ""))))) == 1

    @pytest.mark.asyncio
    async def test_no_store_registered_still_dedups_in_memory(self):
        budget = _budget("dashboard:1")
        assert len(_image_blocks(await budget.apply(_prompt((1, "a.png", ""))))) == 1
        budget.commit()
        assert _image_blocks(await budget.apply(_prompt((1, "a.png", "")))) == []

    @pytest.mark.asyncio
    async def test_text_only_prompts_are_returned_as_is(self):
        blocks = [{"type": "text", "text": "hi"}]
        assert await _budget("k").apply(blocks) is blocks


class TestStagedCommit:
    @pytest.mark.asyncio
    async def test_apply_stages_and_only_commit_records(self, patched_map):
        sm = SessionMap()
        sm.set("dashboard:1", SID)
        image_ledger.set_image_ledger_store(sm)
        budget = _budget("dashboard:1")
        await budget.apply(_prompt((1, "a.png", "")))
        assert sm.get_image_ledger("dashboard:1") == {}, "staged, not recorded"
        budget.commit()
        assert sm.get_image_ledger("dashboard:1")["hashes"] == [image_digest(_data(1))]
        assert sm.get_image_ledger("dashboard:1")["sid"] == SID
        budget.commit()  # nothing staged: a no-op
        await sm.aclose()

    @pytest.mark.asyncio
    async def test_discard_drops_the_stage_for_durable_and_local_ledgers(self, patched_map):
        sm = SessionMap()
        sm.set("dashboard:1", SID)
        image_ledger.set_image_ledger_store(sm)
        durable = _budget("dashboard:1")
        await durable.apply(_prompt((1, "a.png", "")))
        durable.discard()
        durable.commit()
        assert sm.get_image_ledger("dashboard:1") == {}
        local = _budget("subagent:x")
        await local.apply(_prompt((1, "a.png", "")))
        local.discard()
        assert len(_image_blocks(await local.apply(_prompt((1, "a.png", ""))))) == 1
        await sm.aclose()

    @pytest.mark.asyncio
    async def test_a_new_apply_replaces_an_uncommitted_stage(self):
        budget = _budget("k")
        await budget.apply(_prompt((1, "a.png", "")))
        await budget.apply(_prompt((2, "b.png", "")))
        budget.commit()
        assert budget._local["hashes"] == [image_digest(_data(2))]

    @pytest.mark.asyncio
    async def test_reset_clears_durable_local_and_staged_state(self, patched_map):
        sm = SessionMap()
        sm.set("dashboard:1", SID)
        image_ledger.set_image_ledger_store(sm)
        budget = _budget("dashboard:1")
        await budget.apply(_prompt((1, "a.png", "")))
        budget.commit()
        await budget.apply(_prompt((2, "b.png", "")))
        budget.reset()
        budget.commit()
        assert sm.get_image_ledger("dashboard:1") == {}
        assert budget._local == empty_ledger(SID)
        await sm.aclose()


class TestBuilderAnnotation:
    def test_the_builder_annotates_each_image_with_its_source_and_spans(self, tmp_path):
        p = _png(tmp_path, "shot.png")
        blocks = build_prompt_blocks(f"see {p}")
        assert blocks[0]["text"] == "see [image: shot.png]"
        assert blocks[1][IMAGE_BLOCK_SOURCE_KEY] == {
            "path": str(p),
            "spans": [[4, 4 + len("[image: shot.png]")]],
        }

    def test_the_layer_rewrites_the_marker_the_builder_wrote(self, tmp_path, monkeypatch):
        p = _png(tmp_path, "shot.png")
        first = apply_image_budget(build_prompt_blocks(f"see {p}"), None)
        second = apply_image_budget(build_prompt_blocks(f"again {p}"), first.ledger)
        assert _image_blocks(second.blocks) == []
        assert second.blocks[0]["text"] == "again " + _sent("shot.png", str(p))
        monkeypatch.setattr(image_ledger, "MAX_PROMPT_IMAGE_BLOCKS", 0)
        over = apply_image_budget(build_prompt_blocks(f"see {p}"), None)
        assert over.blocks[0]["text"] == "see " + _over("shot.png", str(p))


def _handle(
    tmp_path: Path,
    sent: list[dict],
    *,
    fail_send: bool = False,
    frames: list[JsonRpcMessage] | None = None,
    session_id: str = SID,
) -> AcpSessionHandle:
    """A handle on a fake runtime that records the prompt params and ends the turn.

    A successful send answers itself: the runtime's reader would deliver the
    prompt's result frame (after any ``frames``, which stand in for
    notifications the backend sends first), so the fake queues them as the
    write's side effect -- queueing them before the prompt would have the
    pre-turn stale drain discard them. ``fail_send`` dies at the write instead,
    the shape of a runtime that went away between the build and the write.
    """
    runtime = AcpRuntime(work_dir=str(tmp_path))
    runtime._initialized = True
    runtime._prompt_capabilities = {"image": True}
    queue: asyncio.Queue = asyncio.Queue()
    runtime._session_queues[session_id] = queue

    async def send_request(method, params):
        sent.append(params)
        if fail_send:
            raise AcpRuntimeDead("died before the write")
        req_id = len(sent)
        for frame in frames or []:
            queue.put_nowait(frame)
        queue.put_nowait(
            JsonRpcMessage.from_dict(
                {"jsonrpc": "2.0", "id": req_id, "result": {"stopReason": "end_turn"}}
            )
        )
        return req_id

    runtime.send_request = send_request
    return AcpSessionHandle(session_id, queue, runtime, session_key="dashboard:1")


async def _turn(handle: AcpSessionHandle, message: str) -> None:
    """Drive one prompt turn to its end (the fake send answers it)."""
    async for _event in handle.prompt(message, timeout=3.0):
        pass


async def _turn_dies(handle: AcpSessionHandle, message: str) -> None:
    gen = handle.prompt(message, timeout=3.0)
    with pytest.raises(AcpRuntimeDead):
        await gen.__anext__()
    await gen.aclose()


def _clear_frame(session_id: str = SID) -> JsonRpcMessage:
    return JsonRpcMessage(method=METHOD_CLEAR_STATUS, params={"sessionId": session_id})


class TestHandleWiring:
    @pytest.mark.asyncio
    async def test_the_handle_applies_the_layer_across_turns(self, tmp_path):
        p = _png(tmp_path, "shot.png")
        sent: list[dict] = []
        handle = _handle(tmp_path, sent)
        await _turn(handle, f"first {p}")
        await _turn(handle, f"second {p}")
        assert [b["type"] for b in sent[0]["prompt"]] == ["text", "image"]
        assert [b["type"] for b in sent[1]["prompt"]] == ["text"]
        assert sent[1]["prompt"][0]["text"] == "second " + _sent("shot.png", str(p))

    @pytest.mark.asyncio
    async def test_nothing_host_side_reaches_the_wire(self, tmp_path):
        p = _png(tmp_path, "shot.png")
        sent: list[dict] = []
        await _turn(_handle(tmp_path, sent), f"see {p}")
        for block in sent[0]["prompt"]:
            assert not any(str(k).startswith("_") for k in block), block.keys()

    @pytest.mark.asyncio
    async def test_a_prompt_that_never_reached_the_runtime_charges_nothing(self, tmp_path):
        """The ledger is committed by the WRITE, not the build: a runtime that dies
        between them makes the caller re-queue the same message, and that retry
        must still carry the image instead of a ``sent earlier`` marker."""
        p = _png(tmp_path, "shot.png")
        sent: list[dict] = []
        dead = _handle(tmp_path, sent, fail_send=True)
        await _turn_dies(dead, f"first {p}")
        assert [b["type"] for b in sent[0]["prompt"]] == ["text", "image"]
        # Same handle, same key: the retry must inline again.
        await _turn_dies(dead, f"retry {p}")
        assert [b["type"] for b in sent[1]["prompt"]] == ["text", "image"]
        assert dead._image_budget._local == empty_ledger(SID)

    @pytest.mark.asyncio
    async def test_a_failed_write_leaves_the_durable_record_uncharged(self, tmp_path, patched_map):
        sm = SessionMap()
        sm.set("dashboard:1", SID)
        image_ledger.set_image_ledger_store(sm)
        p = _png(tmp_path, "shot.png")
        sent: list[dict] = []
        await _turn_dies(_handle(tmp_path, sent, fail_send=True), f"see {p}")
        assert sm.get_image_ledger("dashboard:1") == {}
        # A live handle on the same record then sends it for real.
        await _turn(_handle(tmp_path, sent), f"again {p}")
        assert [b["type"] for b in sent[1]["prompt"]] == ["text", "image"]
        assert sm.get_image_ledger("dashboard:1")["hashes"] == [
            image_digest(sent[1]["prompt"][1]["data"])
        ]
        await sm.aclose()

    @pytest.mark.asyncio
    async def test_the_handle_uses_the_durable_record_when_the_session_has_one(
        self, tmp_path, patched_map
    ):
        sm = SessionMap()
        sm.set("dashboard:1", SID)
        image_ledger.set_image_ledger_store(sm)
        p = _png(tmp_path, "shot.png")
        sent: list[dict] = []
        await _turn(_handle(tmp_path, sent), f"see {p}")
        digest = image_digest(sent[0]["prompt"][1]["data"])
        assert sm.get_image_ledger("dashboard:1")["hashes"] == [digest]
        # A NEW handle (a recycled session, same native conversation) dedups.
        await _turn(_handle(tmp_path, sent), f"again {p}")
        assert [b["type"] for b in sent[1]["prompt"]] == ["text"]
        await sm.aclose()

    @pytest.mark.asyncio
    async def test_a_fresh_conversation_behind_a_deferred_promotion_reads_no_old_ledger(
        self, tmp_path, patched_map
    ):
        """Tool-search resume: the entry still records the OLD sid (promotion is
        deferred until the replay-bearing turn lands) while the handle already
        speaks for a NEW empty conversation. Its first prompt must inline the
        picture, and the ledger it writes must survive the later promotion."""
        sm = SessionMap()
        sm.set("dashboard:1", "old-sid")
        image_ledger.set_image_ledger_store(sm)
        p = _png(tmp_path, "shot.png")
        sent: list[dict] = []
        await _turn(_handle(tmp_path, sent, session_id="old-sid"), f"see {p}")
        assert sm.get_image_ledger("dashboard:1")["sid"] == "old-sid"
        # The fresh session (new sid) prompts BEFORE the map records its sid.
        await _turn(_handle(tmp_path, sent, session_id="new-sid"), f"again {p}")
        assert [b["type"] for b in sent[1]["prompt"]] == ["text", "image"], "not deduped"
        assert sm.get_image_ledger("dashboard:1")["sid"] == "new-sid"
        # The landed turn promotes the sid; the new conversation's ledger stays.
        sm.set("dashboard:1", "new-sid")
        await _turn(_handle(tmp_path, sent, session_id="new-sid"), f"third {p}")
        assert [b["type"] for b in sent[2]["prompt"]] == ["text"]
        await sm.aclose()

    @pytest.mark.asyncio
    async def test_a_confirmed_native_clear_forgets_the_inlined_images(self, tmp_path, patched_map):
        """``/clear`` empties the conversation under the SAME sid, so the sid-scoped
        ledger would otherwise still apply; the clear notification this session
        owns must reset it, or a picture attached again would be dropped."""
        sm = SessionMap()
        sm.set("dashboard:1", SID)
        image_ledger.set_image_ledger_store(sm)
        p = _png(tmp_path, "shot.png")
        sent: list[dict] = []
        await _turn(_handle(tmp_path, sent), f"see {p}")
        assert sm.get_image_ledger("dashboard:1")["hashes"], "charged after the write"
        # A turn during which the backend confirms a clear.
        await _turn(_handle(tmp_path, sent, frames=[_clear_frame()]), "wipe it")
        assert sm.get_image_ledger("dashboard:1") == {}
        await _turn(_handle(tmp_path, sent), f"again {p}")
        assert [b["type"] for b in sent[2]["prompt"]] == ["text", "image"]
        await sm.aclose()

    @pytest.mark.asyncio
    async def test_a_fanned_out_clear_is_not_this_sessions_clear(self, tmp_path):
        p = _png(tmp_path, "shot.png")
        sent: list[dict] = []
        handle = _handle(tmp_path, sent)
        await _turn(handle, f"see {p}")
        foreign = _clear_frame()
        foreign.fanout_no_owner = True
        # A second fake runtime delivering the foreign frame, same ledger.
        handle2 = _handle(tmp_path, sent, frames=[foreign])
        handle2._image_budget = handle._image_budget
        await _turn(handle2, "someone else cleared")
        await _turn(handle2, f"again {p}")
        assert [b["type"] for b in sent[2]["prompt"]] == ["text"], "ledger kept"

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
        # A reset onto a fresh native conversation starts over.
        client._session_id = "s10"
        await client._send_prompt(f"three {p}")
        assert [b["type"] for b in sent[2]["prompt"]] == ["text", "image"]

    @pytest.mark.asyncio
    async def test_the_direct_client_charges_nothing_for_a_failed_write(self, tmp_path):
        p = _png(tmp_path, "shot.png")
        client = AcpClient(work_dir=tmp_path, session_key="dashboard:9")
        client._session_id = "s9"
        sent: list[dict] = []

        async def failing(method, params):
            sent.append(params)
            raise AcpRuntimeDead("pipe closed")

        client._send_request = failing
        with pytest.raises(AcpRuntimeDead):
            await client._send_prompt(f"one {p}")
        assert client._image_budget._local == empty_ledger("s9")

        async def working(method, params):
            sent.append(params)
            return 2

        client._send_request = working
        await client._send_prompt(f"retry {p}")
        assert [b["type"] for b in sent[1]["prompt"]] == ["text", "image"]

    @pytest.mark.asyncio
    async def test_a_clear_typed_as_prompt_text_leaves_the_direct_clients_ledger_alone(
        self, tmp_path
    ):
        """Only a confirmed clear notification empties the ledger. ``/clear`` sent as
        prompt text is an ordinary prompt to the ledger: no harness has been measured
        to clear its conversation on that text, and a ledger emptied for one that did
        not would re-send every picture into a history that still holds them -- the
        growth the ledger exists to stop -- while a ledger kept across a clear that did
        happen costs a ``sent earlier`` marker that names the file."""
        p = _png(tmp_path, "shot.png")
        client = AcpClient(work_dir=tmp_path, session_key="dashboard:9")
        client._session_id = "s9"
        sent: list[dict] = []

        async def send_request(method, params):
            sent.append(params)
            return len(sent)

        client._send_request = send_request
        await client._send_prompt(f"one {p}")
        before = dict(client._image_budget._local)
        await client._send_prompt("/clear")
        assert client._image_budget._local == before
        assert [b["type"] for b in sent[1]["prompt"]] == ["text"]
        await client._send_prompt(f"again {p}")
        assert [b["type"] for b in sent[2]["prompt"]] == ["text"]
        assert "sent earlier" in sent[2]["prompt"][0]["text"]

    @pytest.mark.asyncio
    async def test_the_direct_client_empties_the_ledger_on_a_clear_notification(self, tmp_path):
        from unittest.mock import AsyncMock

        p = _png(tmp_path, "shot.png")
        client = AcpClient(work_dir=tmp_path, session_key="dashboard:9")
        client._session_id = "s1"

        async def send_request(method, params):
            return 1

        client._send_request = send_request
        await client._send_prompt(f"one {p}")
        assert client._image_budget._local["hashes"]

        clear_msg = JsonRpcMessage(method=METHOD_CLEAR_STATUS, params={"sessionId": "s1"})
        complete_msg = JsonRpcMessage(id=1, result={"status": "complete"})

        async def fake_prompt_loop(req_id, timeout):
            yield "clear", clear_msg
            yield "complete", complete_msg

        client.ensure_ready = AsyncMock()
        client._send_prompt = AsyncMock(return_value=1)
        client._prompt_loop = fake_prompt_loop
        async for _event in client.stream_events("test"):
            pass
        assert client._image_budget._local == empty_ledger("s1")

    @pytest.mark.asyncio
    async def test_a_clear_typed_as_prompt_text_leaves_the_handles_ledger_alone(self, tmp_path):
        """The shared-runtime writer: the text is a prompt, the notification is the reset."""
        p = _png(tmp_path, "shot.png")
        sent: list[dict] = []
        handle = _handle(tmp_path, sent)
        await _turn(handle, f"see {p}")
        before = dict(handle._image_budget._local)
        await _turn(handle, "/clear")
        assert handle._image_budget._local == before
        await _turn(handle, f"again {p}")
        assert [b["type"] for b in sent[2]["prompt"]] == ["text"]


class TestSpec:
    def test_the_layer_is_listed_in_the_spec_beside_the_per_image_caps(self):
        text = SPEC.read_text(encoding="utf-8")
        assert "image_ledger.py" in text and "SessionImageBudget" in text
