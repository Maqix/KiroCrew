"""The crew main dashboard's gate: the template and the contract name the same fields.

``mypy src/kiro_crew/`` checks both ends of :func:`build_crew_main` -- a
:class:`~kiro_crew.crew_main_contract.CrewMainReads` in, a
:class:`~kiro_crew.crew_main_contract.CrewMainDerived` out, required keys and all. What it
cannot see is ``crew_main.html``, so the HTML end needs a gate of its own. This is that
gate, built after ``test_pipeline_board_contract_parity``.

Scope, stated honestly, because a reader will assume more than is here. The parity case
asserts a field EXISTS on both sides. It does NOT assert the value is right, or that a
person will see it where the template puts it. The cases after it cover what a text gate
cannot: that no branch of the provider can leave a field unanswered, that absence is
three-state in WORDS rather than a zero, that no value is a percentage, and that a model
cannot reach a derived field.

Two attacks an earlier draft of the extractor allowed, each with a case here: a field
named only inside an HTML COMMENT counting as read (``crew_main.html`` opens with a long
comment that names several field concepts in prose), and a broken extractor returning an
empty set -- which would read as "the contract is over-specified" rather than "the gate is
broken". The precondition cases run first for that reason.
"""

from __future__ import annotations

import re
import time

import pytest

from kiro_crew.crew_main_contract import (
    CONTRACT_VERSION,
    DERIVED_FIELDS,
    EMPTY_JUDGMENT,
    FOLD_UNREADABLE,
    JUDGMENT_FIELDS,
    JUDGMENT_TEXT_LIMIT,
    NO_PUBLISHED_VIEW,
    NOT_RECORDED,
    UNREADABLE,
    CrewMainData,
    CrewMainReads,
    build_crew_main,
    card_data_payload,
    merge_crew_main,
    read_crew_main_template,
    template_fields,
    validate_judgment,
)
from kiro_crew.dashboard.card_lifecycle import (
    _JUDGMENT_PROMPT,
    _redact_judgment,
    is_crew_main_slot,
)
from kiro_crew.dashboard.dynamic_cards import (
    MAX_DATA_BYTES,
    MAX_HTML_BYTES,
    normalize_card,
)

FOLD_NAMES = ("status", "usage", "approvals", "work", "panel")


def _reads(**overrides: object) -> CrewMainReads:
    """A board where every fold read and every value is present."""
    base: dict[str, object] = {
        "status": {
            "lifecycle": "open",
            "turn_open": True,
            "turns_completed": 12,
            "turns_refused": 1,
            "entries": 480,
            "agent": "kirocrew-conductor",
            "model": "a-model",
            "last_time": int(time.time() * 1000),
        },
        "usage": {
            "credits": 3.5,
            "credits_by_source": {"subagent": {"credits": 1.25, "reported": 4}},
            "tokens": {"total": 1215},
        },
        "approvals": {
            "requested": 9,
            "decided": 7,
            "pending": 2,
            "by_decision": {"allow": 6, "deny": 1},
        },
        "work": {
            "items": [
                {"state": "open", "status": "progress"},
                {"state": "open", "status": "blocked"},
                {"state": "open", "status": "question"},
                {"state": "accepted", "status": "done"},
                {"state": "rejected", "status": None},
            ],
            "omitted": 2,
        },
        "panel": {"template": "a-template", "title": "Fleet board", "publishes": 7},
    }
    base.update(overrides)
    return base  # type: ignore[return-value]


# --------------------------------------------------------------------------
# preconditions -- a broken extractor must not read as a clean contract
# --------------------------------------------------------------------------


def test_the_extractor_finds_fields_at_all() -> None:
    """An empty set would make every parity case below pass vacuously."""
    assert len(template_fields(read_crew_main_template())) == len(CrewMainData.__annotations__)
    assert len(CrewMainData.__annotations__) > 1


def test_the_extractor_ignores_a_field_named_only_in_a_comment() -> None:
    """The real template opens with a comment that names field concepts in prose."""
    markup = '<!-- data-dashboard-field="ghost" --><span data-dashboard-field="real"></span>'
    assert template_fields(markup) == {"real"}


def test_the_extractor_ignores_a_field_with_no_name() -> None:
    assert template_fields("<span data-dashboard-field></span>") == frozenset()


# --------------------------------------------------------------------------
# the parity gate
# --------------------------------------------------------------------------


def test_the_template_and_the_contract_name_the_same_fields() -> None:
    fields = template_fields(read_crew_main_template())
    declared = frozenset(CrewMainData.__annotations__)
    assert fields - declared == frozenset(), "the template paints a field no type declares"
    assert declared - fields == frozenset(), "the contract declares a field nothing paints"


def test_every_contract_field_has_exactly_one_writer() -> None:
    assert DERIVED_FIELDS & JUDGMENT_FIELDS == frozenset()
    assert DERIVED_FIELDS | JUDGMENT_FIELDS == frozenset(CrewMainData.__annotations__)
    assert JUDGMENT_FIELDS == {"lede", "you", "notes"}
    assert CONTRACT_VERSION == 1


def test_the_template_is_inert() -> None:
    """No script, no control, no navigation, no remote byte -- read off the markup.

    The host strips these too, but a template that needs stripping is a template one
    render-path change away from shipping them. This asserts the file itself is clean.
    """
    # Comments STRIPPED first, the same precaution the parity extractor takes: the
    # template's opening comment explains in prose which CSS the host removes, and prose
    # naming a forbidden token is documentation rather than a declaration.
    markup = re.sub(r"<!--.*?-->", " ", read_crew_main_template(), flags=re.S).lower()
    assert "<div" in markup, "comment stripping ate the markup"
    for token in ("<script", "<form", "<button", "<input", "<select", "<textarea", "<iframe"):
        assert token not in markup, f"the template carries {token}"
    for token in ("href=", "src=", "srcset=", "onclick", "javascript:", "@import", "@font-face"):
        assert token not in markup, f"the template carries {token}"
    # CSS generated text is stripped by the host in card mode, so a label declared that
    # way would vanish from the page AND from the backend's text scan of it.
    for token in ("content:", "list-style", "quotes:", "text-emphasis"):
        assert token not in markup, f"the template declares {token}, which the host strips"


def test_the_template_says_nothing_on_it_is_a_link() -> None:
    """A ruling: if something reads as a link, the card says in words that it is not."""
    assert "link" in read_crew_main_template().lower()


# --------------------------------------------------------------------------
# the provider is TOTAL: no branch can leave a field unanswered
# --------------------------------------------------------------------------


@pytest.mark.parametrize("unreadable", [(), *[(name,) for name in FOLD_NAMES], FOLD_NAMES])
def test_every_field_is_answered_whatever_could_not_be_read(unreadable: tuple[str, ...]) -> None:
    """Each fold failing alone, all of them failing, and none -- every field present.

    This is the case that would have caught a section helper whose early return dropped
    a key: an absent key binds nothing and the template paints an EMPTY cell, which a
    reader cannot tell from a recorded zero.
    """
    derived = build_crew_main(_reads(**{name: FOLD_UNREADABLE for name in unreadable}))
    assert frozenset(derived) == DERIVED_FIELDS
    for field, value in derived.items():
        assert isinstance(value, str) and value.strip(), f"{field} answered nothing"


@pytest.mark.parametrize(
    "reads",
    [
        pytest.param(_reads(**{name: {} for name in FOLD_NAMES}), id="folds-read-but-empty"),
        pytest.param(_reads(work={"items": []}), id="board-with-no-items"),
        pytest.param(_reads(work={}), id="board-with-no-item-list"),
        pytest.param(_reads(usage={"credits_by_source": {}}), id="no-credit-split"),
        pytest.param(_reads(panel={"template": ""}), id="nothing-published"),
        pytest.param(_reads(status={"lifecycle": "unknown"}), id="log-no-longer-says"),
    ],
)
def test_every_field_is_answered_for_a_thin_fold(reads: CrewMainReads) -> None:
    derived = build_crew_main(reads)
    assert frozenset(derived) == DERIVED_FIELDS
    for field, value in derived.items():
        assert isinstance(value, str) and value.strip(), f"{field} answered nothing"


def test_a_damaged_value_costs_one_field_and_not_the_panel() -> None:
    """Bytes off a file the reader does not control must not raise.

    Each of these is a value a damaged or planted line can carry: a non-finite credit
    total, a bool where a count belongs, and a negative count. The line stays on disk
    and nothing rewrites it, so a raise here would be permanent for that crew.
    """
    derived = build_crew_main(
        _reads(
            status={"lifecycle": "open", "turns_completed": True},
            usage={"credits": float("nan"), "tokens": {"total": -5}},
            approvals={"pending": True, "requested": 9},
        )
    )
    assert derived["turns"] == NOT_RECORDED
    assert derived["tokens"] == NOT_RECORDED
    assert derived["approvals_open"] == NOT_RECORDED
    assert "nan" not in derived["credits"].lower()


@pytest.mark.parametrize(
    ("stamp", "raises"),
    [
        pytest.param(10**20, "OSError", id="errno-value-too-large"),
        pytest.param(-(10**20), "OSError", id="negative-value-too-large"),
        pytest.param(10**30, "OverflowError", id="past-platform-time_t"),
        pytest.param(float("inf"), "OverflowError", id="infinite"),
    ],
)
def test_a_damaged_stamp_costs_one_field_whichever_way_it_breaks(stamp: float, raises: str) -> None:
    """One case per exception ``fromtimestamp`` raises, because they are not one branch.

    A single value exercises ONE of them: ``10**20`` raises ``OSError`` here and
    ``10**30`` raises ``OverflowError``, so a catch narrowed to either alone still passes
    a suite that only tried the other. (``ValueError`` is the third spelling, raised for a
    year out of range on platforms whose ``fromtimestamp`` checks that instead; it is kept
    in the catch and is not reproducible on this one.)
    """
    derived = build_crew_main(_reads(status={"lifecycle": "open", "last_time": stamp}))
    assert derived["last_activity"] == NOT_RECORDED


# --------------------------------------------------------------------------
# absence is three-state, in words
# --------------------------------------------------------------------------


def test_an_unread_fold_and_an_empty_one_read_differently() -> None:
    """The distinction the whole contract rests on: unknown is not the same as none."""
    unread = build_crew_main(_reads(**{name: FOLD_UNREADABLE for name in FOLD_NAMES}))
    empty = build_crew_main(_reads(**{name: {} for name in FOLD_NAMES}))
    assert set(unread.values()) == {UNREADABLE}
    assert UNREADABLE not in set(empty.values())
    assert empty["state"] == NOT_RECORDED
    assert empty["published_view"] == NO_PUBLISHED_VIEW
    for field in DERIVED_FIELDS:
        assert unread[field] != empty[field], f"{field} cannot tell unknown from none"


def test_no_value_is_ever_a_bare_number_or_a_zero() -> None:
    """A bare count invites the reader to supply the total, and they supply a wrong one."""
    for reads in (_reads(), _reads(**{name: {} for name in FOLD_NAMES})):
        for field, value in build_crew_main(reads).items():
            assert not value.strip().isdigit(), f"{field} is a bare number"
            assert any(ch.isalpha() for ch in value), f"{field} carries no words"


def test_no_value_is_a_percentage() -> None:
    values = list(build_crew_main(_reads()).values())
    assert not [v for v in values if "%" in v or "percent" in v.lower()]


def test_every_count_with_a_denominator_states_it() -> None:
    """A ruling, asserted: the total is on the page, not in the reader's head."""
    derived = build_crew_main(_reads())
    for field in (
        "items_open",
        "items_progress",
        "items_blocked",
        "items_done",
        "items_question",
        "approvals_open",
    ):
        assert " of " in derived[field], f"{field} states a count with no denominator"


def test_a_worker_claim_is_not_reported_as_an_acceptance() -> None:
    """``items_done`` counts the conductor's ruling, and says the word.

    Two items report ``done`` and neither is accepted, so a provider that counted the
    worker's own status would say two.
    """
    derived = build_crew_main(
        _reads(
            work={
                "items": [
                    {"state": "open", "status": "done"},
                    {"state": "open", "status": "done"},
                    {"state": "accepted", "status": "done"},
                ],
                "omitted": 0,
            }
        )
    )
    assert derived["items_done"] == "1 of 3 items accepted"


def test_dropped_log_entries_are_never_added_into_a_board_total() -> None:
    """One dropped ENTRY is not one missing ITEM, so the two counts stay apart."""
    derived = build_crew_main(_reads(work={"items": [], "omitted": 4}))
    assert "4" in derived["board_omitted"]
    assert "4" not in derived["items_open"]


def test_an_unmetered_subagent_bucket_is_not_a_charge_of_zero() -> None:
    derived = build_crew_main(
        _reads(
            usage={
                "credits": 1.0,
                "credits_by_source": {"subagent": {"credits": 0.0, "reported": 0}},
            }
        )
    )
    assert derived["credits_subagents"] == "no sub-agent charge reported"


def test_a_credit_charge_is_never_shown_in_scientific_notation() -> None:
    derived = build_crew_main(_reads(usage={"credits": 0.00001}))
    assert "e-" not in derived["credits"]
    assert "0.00001" in derived["credits"]


# --------------------------------------------------------------------------
# the model cannot reach a number
# --------------------------------------------------------------------------


def test_a_model_field_can_never_overwrite_a_derived_one() -> None:
    """The merge names every field, so a judgment carrying a count cannot land.

    mypy rejects the extra key in a ``CrewMainJudgment`` literal, which is why this
    passes the hostile payload through ``validate_judgment`` -- the runtime door a model
    actually arrives at.
    """
    derived = build_crew_main(_reads())
    hostile = validate_judgment(
        {
            "lede": "ok",
            "credits": "999999 credits billed to this crew",
            "items_open": "0 of 0 items still open",
            "state": "closed",
        }
    )
    assert frozenset(hostile) == JUDGMENT_FIELDS
    merged = merge_crew_main(derived, hostile)
    for field in DERIVED_FIELDS:
        assert merged[field] == derived[field], f"a model reached {field}"
    assert merged["lede"] == "ok"


def test_the_merge_ignores_a_derived_key_even_when_one_reaches_it() -> None:
    """The SECOND defence, pinned on its own.

    The case above proves ``validate_judgment`` strips a numeric key at the door, which
    means it never reaches the merge -- so it says nothing about whether the merge would
    honour one. Two defences, and a suite that only exercises the outer one passes a
    merge rewritten as ``{**derived, **judgment}``.

    mypy rejects the extra key in a ``CrewMainJudgment`` literal, which is the third
    defence and the reason for the ignore: this hands the merge the payload the type
    system forbids, to assert the runtime does not honour it either.
    """
    derived = build_crew_main(_reads())
    forged = {
        **EMPTY_JUDGMENT,
        "lede": "ok",
        "credits": "999999 credits billed to this crew",
        "items_open": "0 of 0 items still open",
        "state": "closed",
        "published_view": "a view that was never published",
    }
    merged = merge_crew_main(derived, forged)  # type: ignore[arg-type]
    for field in DERIVED_FIELDS:
        assert merged[field] == derived[field], f"the merge honoured a forged {field}"
    assert merged["lede"] == "ok"
    assert frozenset(merged) == frozenset(CrewMainData.__annotations__)


def test_a_judgment_is_bounded_and_single_line() -> None:
    judgment = validate_judgment({"lede": "x" * 500, "you": "two\nlines  here", "notes": 17})
    assert len(judgment["lede"]) == JUDGMENT_TEXT_LIMIT
    assert judgment["you"] == "two lines here"
    assert judgment["notes"] == ""


@pytest.mark.parametrize("raw", [None, [], "a string", 17, {"lede": None}])
def test_a_malformed_model_reply_still_yields_three_empty_sentences(raw: object) -> None:
    """The panel publishes either way: the numbers must never wait on a model."""
    assert validate_judgment(raw) == EMPTY_JUDGMENT


def test_a_credential_in_a_sentence_empties_that_sentence_alone() -> None:
    """Redacted prose is a sentence written about a value that is now gone."""
    secret = "AKIA" + "Q" * 16
    judgment = _redact_judgment(
        '{"lede": "The key is ' + secret + '", "you": "Rule on it.", "notes": ""}'
    )
    assert judgment["lede"] == ""
    assert judgment["you"] == "Rule on it."


def test_the_judgment_prompt_asks_for_three_sentences_and_forbids_numbers() -> None:
    """A sentence carrying a count is a number the merge cannot catch."""
    prompt = _JUDGMENT_PROMPT
    for field in JUDGMENT_FIELDS:
        assert field in prompt
    assert "NO NUMBERS" in prompt
    assert str(JUDGMENT_TEXT_LIMIT) in prompt
    # The layout is not the model's on this path. Asserted on the JSON SHAPE the prompt
    # requires rather than on the word "html" anywhere in it: the prompt also FORBIDS
    # html in prose, and a scan for the word cannot tell a prohibition from a request.
    schema = next(line for line in prompt.splitlines() if line.startswith("Return ONLY JSON"))
    assert schema.count('": "') == len(JUDGMENT_FIELDS)
    for banned in ("html", "data", "field"):
        assert banned not in schema


# --------------------------------------------------------------------------
# the payload the host actually accepts
# --------------------------------------------------------------------------


def test_the_published_card_fits_the_host_caps_at_their_worst() -> None:
    """Every judgment field at its cap, every fold unreadable, and still normalizable."""
    long_judgment = validate_judgment({field: "x" * 400 for field in JUDGMENT_FIELDS})
    for reads in (_reads(), _reads(**{name: FOLD_UNREADABLE for name in FOLD_NAMES})):
        markup = read_crew_main_template()
        data = card_data_payload(merge_crew_main(build_crew_main(reads), long_judgment))
        assert len(markup.encode()) <= MAX_HTML_BYTES
        size = sum(len(k.encode()) + len(v.encode()) for k, v in data.items())
        assert size <= MAX_DATA_BYTES, f"{size} data bytes"
        assert normalize_card({"html": markup, "data": data}) is not None


def test_every_field_name_is_one_the_host_accepts() -> None:
    """``normalize_card`` refuses a name outside its grammar, silently dropping the card."""
    from kiro_crew.dashboard.dynamic_cards import _FIELD_NAME

    for field in CrewMainData.__annotations__:
        assert _FIELD_NAME.fullmatch(field), field


# --------------------------------------------------------------------------
# whose panel this is
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("member-bolin", True),
        ("member-bolin.memory-private", True),
        ("chat-1875-1790253512", False),
        ("", False),
        ("membership-drive", False),
    ],
)
def test_only_a_crew_members_main_slot_takes_the_derived_panel(key: str, expected: bool) -> None:
    """The gate is the members module's own spelling, including the store suffix."""
    assert is_crew_main_slot(type("S", (), {"key": key})()) is expected


def test_the_gate_is_the_members_modules_predicate_and_not_a_second_parse() -> None:
    """A second parse of the same key is how two readers come to disagree about it."""
    import inspect

    from kiro_crew.dashboard import card_lifecycle

    source = inspect.getsource(card_lifecycle.is_crew_main_slot)
    assert "slug_from_dm_slot_key" in source
    assert "startswith" not in source and "split" not in source
