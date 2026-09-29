"""Every registered setup action reaches every layer a setup card passes through.

A setup-card kind is one module in ``kiro_crew/setup_actions/``, but the card also
needs the card store to accept its kind, governance to gate it, the dashboard to
draw it and title it, and the crew-setup skill to tell the agent how to propose
it. Nothing but this file notices a kind that skipped one of those: the card
would store, and then render as the generic fallback, or never be proposed. So
each layer is read here the way the other parity gates read theirs (the TS
source parsed, not imported), in both directions, and each failure names the
kind, the layer and the file to edit. The steps: ``first-run.md``, "Adding a
setup action".
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from kiro_crew import setup_actions
from kiro_crew import setup_cards as sc
from kiro_crew.platform.governance import SCOPE_CATALOG

_ROOT = Path(__file__).resolve().parents[1]
_REGISTRY_TSX = _ROOT / "website" / "src" / "components" / "setup" / "setupCardRegistry.tsx"
_EN_MANUAL = _ROOT / "website" / "src" / "i18n" / "locales" / "en.manual.json"
_SKILL = _ROOT / "src" / "kiro_crew" / "builtin_skills" / "crew-setup" / "SKILL.md"

_REGISTERED = sorted(a.kind for a in setup_actions.ACTIONS)
_PROPOSABLE = sorted(a.kind for a in setup_actions.proposable())


def _rel(path: Path) -> str:
    return str(path.relative_to(_ROOT))


def _ts_object_body(name: str) -> str:
    """The text between ``export const NAME = {`` and its closing top-level ``}``."""
    text = _REGISTRY_TSX.read_text(encoding="utf-8")
    m = re.search(rf"^export const {name}\b[^=]*=\s*\{{\n(?P<body>.*?)^\}}", text, re.M | re.S)
    assert m, f"{_rel(_REGISTRY_TSX)} no longer declares `export const {name} = {{ ... }}`"
    return m.group("body")


def _frontend_kinds() -> list[str]:
    """The top-level keys of ``SETUP_CARD_KINDS`` (one entry per kind, two-space indented)."""
    return re.findall(r"^  ([a-z_]+):", _ts_object_body("SETUP_CARD_KINDS"), re.M)


def _frontend_title_keys() -> dict[str, str]:
    body = _ts_object_body("SETUP_CARD_TITLE_KEY")
    return dict(re.findall(r"^  ([a-z_]+):\s*'([^']+)'", body, re.M))


def _catalog_keys() -> set[str]:
    def flat(obj: dict, prefix: str = "") -> set[str]:
        out: set[str] = set()
        for key, value in obj.items():
            dotted = f"{prefix}.{key}" if prefix else key
            out |= flat(value, dotted) if isinstance(value, dict) else {dotted}
        return out

    return flat(json.loads(_EN_MANUAL.read_text(encoding="utf-8")))


def _skill_kinds() -> list[str]:
    """The kinds in the crew-setup skill's ``setup_card`` table (``## Tool reference``)."""
    text = _SKILL.read_text(encoding="utf-8")
    _, found, section = text.partition("## Tool reference")
    assert found, f"{_rel(_SKILL)} has no '## Tool reference' section"
    section = section.split("\n## ", 1)[0]
    return re.findall(r"^\|\s*`([a-z_]+)`\s*\|", section, re.M)


class TestTheParsersFoundSomething:
    """Guard the guard: a shape change that defeats a regex must not read as parity."""

    def test_each_layer_parsed_to_a_nonempty_list(self):
        assert _frontend_kinds(), f"parsed no SETUP_CARD_KINDS entries in {_rel(_REGISTRY_TSX)}"
        assert _frontend_title_keys(), f"parsed no title keys in {_rel(_REGISTRY_TSX)}"
        assert _skill_kinds(), f"parsed no tool-table rows in {_rel(_SKILL)}"
        assert len(_catalog_keys()) > 1000, f"{_rel(_EN_MANUAL)} did not parse as a catalog"

    def test_no_layer_lists_a_kind_twice(self):
        for name, kinds in (
            ("SETUP_CARD_KINDS", _frontend_kinds()),
            ("the skill's tool table", _skill_kinds()),
            ("the registry", [a.kind for a in setup_actions.ACTIONS]),
        ):
            twice = sorted({k for k in kinds if kinds.count(k) > 1})
            assert not twice, f"{name} lists {twice} more than once"


class TestTheCardStore:
    """``setup_cards`` drops a stored record whose kind it does not accept."""

    def test_the_store_accepts_exactly_the_registered_kinds(self):
        missing = sorted(set(_REGISTERED) - sc.CARD_KINDS)
        extra = sorted(sc.CARD_KINDS - set(_REGISTERED))
        assert not missing, (
            f"setup kinds {missing} are registered but setup_cards.CARD_KINDS does not "
            "accept them: add a KIND_ constant and put it in CARD_KINDS "
            "(src/kiro_crew/setup_cards.py)"
        )
        assert not extra, (
            f"setup_cards.CARD_KINDS accepts {extra}, which no module in "
            "src/kiro_crew/setup_actions/ registers: add the action, or drop the kind"
        )

    def test_the_proposable_kinds_agree(self):
        assert sorted(sc.PROPOSABLE_KINDS) == _PROPOSABLE, (
            f"setup_cards.PROPOSABLE_KINDS is {sorted(sc.PROPOSABLE_KINDS)} but the registry's "
            f"agent-proposable kinds are {_PROPOSABLE}; a gateway-only kind is "
            "SetupAction(proposable=False) and is left out of PROPOSABLE_KINDS"
        )


class TestGovernance:
    def test_the_setup_scope_is_a_catalog_row_that_matches_the_kind(self):
        spec = SCOPE_CATALOG.get(setup_actions.SETUP_SCOPE)
        assert spec is not None, (
            f"{setup_actions.SETUP_SCOPE!r} is not a SCOPE_CATALOG row "
            "(src/kiro_crew/platform/governance.py); every setup card is gated by it"
        )
        assert "kinds" in spec.scope_matchers, (
            f"{setup_actions.SETUP_SCOPE!r} lost its inner 'kinds' ruleset, which is how a "
            "policy refuses one card kind"
        )

    @pytest.mark.parametrize("kind", _REGISTERED)
    def test_every_scope_a_kind_answers_to_is_a_catalog_row(self, kind: str):
        action = setup_actions.get(kind)
        assert action is not None
        assert (
            setup_actions.SETUP_SCOPE in action.scopes
        ), f"setup kind {kind!r} does not list {setup_actions.SETUP_SCOPE!r} in its scopes"
        unknown = [s for s in action.scopes if s not in SCOPE_CATALOG]
        assert not unknown, (
            f"setup kind {kind!r} answers to {unknown}, which SCOPE_CATALOG has no row for "
            "(src/kiro_crew/platform/governance.py)"
        )


class TestTheDashboard:
    @pytest.mark.parametrize("kind", _REGISTERED)
    def test_every_kind_has_a_card_entry(self, kind: str):
        assert kind in _frontend_kinds(), (
            f"setup kind {kind!r} has no entry in SETUP_CARD_KINDS ({_rel(_REGISTRY_TSX)}); "
            "the dashboard would draw it as the generic fallback card"
        )

    @pytest.mark.parametrize("kind", _REGISTERED)
    def test_every_kind_has_a_title_in_the_catalog(self, kind: str):
        keys = _frontend_title_keys()
        assert (
            kind in keys
        ), f"setup kind {kind!r} has no title key in SETUP_CARD_TITLE_KEY ({_rel(_REGISTRY_TSX)})"
        assert keys[kind] in _catalog_keys(), (
            f"setup kind {kind!r} is titled {keys[kind]!r}, which {_rel(_EN_MANUAL)} does not "
            "define; add it there and to every translation catalog"
        )

    def test_no_card_entry_without_a_registered_kind(self):
        stray = sorted(set(_frontend_kinds()) - set(_REGISTERED))
        assert not stray, (
            f"SETUP_CARD_KINDS ({_rel(_REGISTRY_TSX)}) has entries for {stray}, which no "
            "module in src/kiro_crew/setup_actions/ registers"
        )
        stray_titles = sorted(set(_frontend_title_keys()) - set(_REGISTERED))
        assert not stray_titles, (
            f"SETUP_CARD_TITLE_KEY ({_rel(_REGISTRY_TSX)}) titles {stray_titles}, which no "
            "module in src/kiro_crew/setup_actions/ registers"
        )


class TestTheSkill:
    @pytest.mark.parametrize("kind", _PROPOSABLE)
    def test_every_proposable_kind_has_a_row_in_the_tool_table(self, kind: str):
        assert kind in _skill_kinds(), (
            f"setup kind {kind!r} has no row in the crew-setup skill's tool table "
            f"({_rel(_SKILL)}, '## Tool reference'); add "
            f"'| `{kind}` | <its setup_card arguments> |' so the agent knows how to propose it"
        )

    def test_no_row_for_a_kind_the_agent_may_not_propose(self):
        stray = sorted(set(_skill_kinds()) - set(_PROPOSABLE))
        assert not stray, (
            f"the crew-setup skill's tool table ({_rel(_SKILL)}) has rows for {stray}, which "
            "the registry does not let the agent propose (unregistered, or gateway-only)"
        )
