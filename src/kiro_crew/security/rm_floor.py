"""Recursive-force ``rm`` seatbelt behind the two ``local-destructive-rm-rf-*`` rows.

Refuses the common respellings of ``rm -rf /`` and ``rm -rf ~`` the rows miss. A
seatbelt, not a parser: other shell shapes stay with the rows and the OS sandbox.
"""

from __future__ import annotations

import re

from ..trust_patterns import ENV_ASSIGNMENT_RE
from . import shell_normalizer as _shell
from .denied_rules import BUILTIN_DENIED_RULES
from .shell_normalizer_span import _close_paren_walk

#: A word that is a ``NAME=`` or ``NAME+=`` assignment prefix. Matched only at a
#: word's START (an unquoted name), so ``"x"=1`` and a bare command never count.
_ASSIGNMENT_WORD_RE = re.compile(r"[A-Za-z_]\w*\+?=")

_RM_FLOOR_RULE_IDS = {
    "root": "local-destructive-rm-rf-root",
    "home": "local-destructive-rm-rf-home",
}
_RM_FLOOR_BY_KIND: dict[str, tuple[str, str]] = {
    kind: (rule.id, rule.pattern)
    for kind, rule_id in _RM_FLOOR_RULE_IDS.items()
    for rule in BUILTIN_DENIED_RULES
    if rule.id == rule_id
}
_RM_FLOOR_NOTE = "Matched on the rm flags and target, not the pattern text: recursive and force against this directory itself."
_RM_FLOOR_UNREAD_NOTE = "Refused because the rm check could not read this command. Simplify it."
_CAN_FIRE_RE = re.compile(r"r[^\w\s]*m")  # a literal rm, quote splices allowed
_RM_WORD_RE = re.compile(r"(?<![\w.-])r[^\w\s/]*m(?![\w.-])")  # rm as its own word

#: A redirection is one word, not the program: an optional ``{fd}`` or fd number,
#: a redirection operator (``>``, ``>>``, ``<``, ``<>``, ``&>``, ``2>&`` …) and any
#: glued target. bash applies a leading redirection before running the command, so
#: one must be stepped over (with its target) exactly as a ``VAR=val`` assignment is.
#: :func:`shell_normalizer._push_token_redirection` is the shared recognizer that
#: reports ``(is_redirection, consumes_next_word)``; it reads a WHOLE token, so
#: :func:`_segments` keeps a redirection and its fd prefix in one word (below).
_HOME_WORDS = frozenset({"~", "$HOME", "${HOME}"})
_CUT_CHARS = frozenset(";&|\n`()")
#: A STANDALONE fd prefix (a bare number or ``{name}``) directly before a redirection
#: operator: ``2>`` and ``{fd}>`` redirect fd 2 / a named fd, but the ``2`` in
#: ``/tmp/2>x`` is part of the path, so the prefix counts only at a word boundary.
_FD_PREFIX_RE = re.compile(r"(?:^|(?<=[\s\x00]))(?:\{[A-Za-z_]\w*\}|\d+)\Z")


def _pull_trailing_fd_prefix(seg: "list[str]") -> str:
    """Remove and return a standalone fd prefix at the end of *seg*, else ``""``.

    Keeps ``2>``/``{fd}>`` as one redirection word rather than splitting the fd
    number off as its own word (which would be read as the program).
    """
    joined = "".join(seg)
    match = _FD_PREFIX_RE.search(joined)
    if match is None:
        return ""
    seg[:] = [joined[: match.start()]]
    return joined[match.start() :]


class _UnreadSpan(ValueError):
    """A double-quoted ``$(`` span the reader could not close before the text
    ended. The gate's floor catches it and refuses with the unread note."""


def _can_fire(command: str) -> bool:
    return bool(_CAN_FIRE_RE.search(command))


def _names_rm_word(command: str) -> bool:
    """``rm`` as its own word in *command*, or once ``${X:-…}`` defaults are resolved."""
    resolved = _shell._resolve_param_defaults(command)
    return bool(_RM_WORD_RE.search(command) or _RM_WORD_RE.search(resolved))


def _target_kind(word: str) -> "str | None":
    """``root`` / ``home`` when *word* is that dir itself, with an optional ``/`` or ``/*``."""
    base = word[:-2] if word.endswith("/*") else word
    base = base.rstrip("/") if base.strip("/") else ""
    if word.startswith("/") and base == "":
        return "root"
    return "home" if base in _HOME_WORDS else None


def _rm_operand_position(text: str) -> bool:
    """True when *text* -- a segment up to a double-quoted ``$(`` -- ends in
    OPERAND position: a command word has already completed, and the still-open
    word that holds the ``$(`` is an ordinary argument, not an assignment value,
    a redirection operator, or a redirection target.

    The capped-span fail-closed synthesises a ``~`` so a ``~`` a capped,
    unreadable ``$(…)`` could hide as an ``rm`` OPERAND is still seen as a home
    target. That only holds where the span IS an operand. A LEADING redirection
    (``>"$(…capped…)" rm -fr ~``) puts the span in the redirection TARGET: there
    the old not-an-assignment check let the fake ``~`` go in, and
    ``_rm_wipe_targets`` stepped the ``>`` redirection over it, took the
    ``~`` as the program, and skipped the segment -- so the real ``rm -fr ~``
    after it was never read and bash ran a home wipe. So the fake operand is
    appended ONLY here. The open word is classified exactly as bash orders a
    simple command: assignment prefixes, then redirections (each with its own
    glued or separate target), then the command word, then operands -- and the
    synthetic ``~`` is an operand, so it needs a completed command word before
    it and an open word that is itself an operand."""
    in_single = in_double = False
    word_open = False
    word_start = 0
    skip_target = False
    command_done = False

    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if in_single:
            if c == "'":
                in_single = False
            i += 1
            continue
        if c == "\\":
            if not word_open:
                word_open, word_start = True, i
            i += 2
            continue
        if c in "'\"":
            if not word_open:
                word_open, word_start = True, i
            if c == "'":
                if not in_double:
                    in_single = True
            elif not in_single:
                in_double = not in_double
            i += 1
            continue
        if c in " \t" and not in_double:
            if word_open:
                word = text[word_start:i]
                if skip_target:
                    skip_target = False  # consumed a separate redirection target
                elif _ASSIGNMENT_WORD_RE.match(word):
                    pass  # an assignment prefix, before the command word
                else:
                    is_redirection, skip_target = _shell._push_token_redirection(word)
                    if not is_redirection:
                        command_done = True  # the command word (or an operand) completed
            word_open = False
            i += 1
            continue
        if not word_open:
            word_open, word_start = True, i
        i += 1
    if not word_open:
        return False
    word = text[word_start:]
    if skip_target or _ASSIGNMENT_WORD_RE.match(word):
        return False  # a redirection target, or an assignment value
    if _shell._push_token_redirection(word)[0]:
        return False  # a redirection operator (with a glued target)
    return command_done


def _segments(command: str) -> "list[str]":
    """*command* cut at separators the shared quote machine marks live; stops at a heredoc."""
    out: list[str] = []
    seg: list[str] = []
    in_redirect = False
    pos, state = 0, 0
    while pos < len(command):
        text, resume, resume_state = command[pos:], len(command), 0
        for step in _shell._iter_shell_chars(text, state):
            if step.active and text.startswith("<<", step.offset):
                break  # heredoc bodies are data; the rows and the sandbox own them
            if step.active and step.char == "#" and _shell._opens_comment(text, step.offset):
                newline = text.find("\n", step.offset)  # a comment's quotes open nothing
                resume = len(command) if newline < 0 else pos + newline
                break
            if step.state == 2 and text.startswith("$(", step.offset):
                # bash reads "$(…)" from an unquoted start, then the double quote resumes
                body = text[step.offset + 2 :]
                hits: list[bool] = []
                close, proven, _, capped = _close_paren_walk(body, arith_hits=hits)
                # A span the reader could not prove hid wherever its real ``)``
                # and the operand after it are. When it ran to the END of its
                # text -- a heredoc whose delimiter would not parse or never
                # terminated -- the walk would also resume at the end and drop
                # every command after the real ``)``. So that case refuses the
                # WHOLE command (the gate reads it as "could not read it"),
                # unless the arithmetic rule below finds the real ``)``.
                # A capped span closed at a GUESS keeps segmenting after the
                # guess, and its output is read as a home operand, which fails
                # closed. But only in operand or command position -- a span that
                # is a ``NAME="`` assignment VALUE feeds a variable, not an
                # ``rm``, so synthesising a ``~`` there instead inserts a word
                # before a real ``rm`` later in the segment and that ``rm`` is
                # then read as an argument of the ``~``, not as the command, so
                # its home wipe is missed.
                #
                # A source ``<<`` after an arithmetic opener (``hits``) may be a
                # shift, so the heredoc-free reading is checked too. Same close:
                # no doubt. Heredoc reading runs to the END: bash shifts, or the
                # heredoc never ends and the syntax error runs nothing, so the
                # shift close is used and its text read with no heredocs (an
                # ``rm`` after ``$((1<<2))⏎`` is a command). Any other mismatch
                # (``"$(echo $((1<<X))⏎)" ~ ; : '⏎X⏎)'`` hides the ``~``) refuses.
                shifted = False
                if not proven and (hits or close >= len(body)):
                    a_close, a_proven, _, _ = _close_paren_walk(body, heredocs=False)
                    if hits and close >= len(body) and a_proven:
                        close, proven, shifted = a_close, True, True
                    elif close >= len(body) or not (hits and a_close == close):
                        raise _UnreadSpan(command)
                inner = body[: close - 1 if proven else close]
                out.extend(_segments(inner.replace("<<", "  ") if shifted else inner))
                fail_closed = not proven and capped
                seg.append('\0" ~ "' if fail_closed and _rm_operand_position("".join(seg)) else "\0")
                in_redirect = False
                resume, resume_state = pos + step.offset + 2 + close, 2
                break
            if step.state == 2 and step.char == "`" and len(step.text) == 1:
                # a backtick body in double quotes is read on its own, then the quote resumes
                closer = _shell._backtick_closer(text, step.offset + 1)
                closed = closer != -1
                end = closer + 1 if closed else len(text)
                out.extend(_segments(text[step.offset + 1 : end - closed]))
                seg.append("\0")
                in_redirect = False
                resume, resume_state = pos + end, 2
                break
            if step.active and in_redirect and step.char == "&":
                seg.append(step.text)  # fd-dup operator (``2>&1``), not a command separator
            elif step.active and step.char in _CUT_CHARS:
                out.append("".join(seg))
                seg = []
                in_redirect = False
            elif step.active and step.char in "<>":
                if in_redirect:
                    seg.append(step.text)  # a second operator char (``>>``, ``<>``)
                else:
                    # a live redirection starts its own word; keep its fd prefix and
                    # operator together so the whole redirection is one token
                    seg.append(" " + _pull_trailing_fd_prefix(seg) + step.text)
                    in_redirect = True
            elif step.char in "$~" and (
                len(step.text) == 2 or step.state == 1 or (step.char == "~" and step.state)
            ):
                seg.append("\0")  # an escaped, single-quoted $ or any quoted ~ is literal
                in_redirect = False
            else:
                seg.append(step.text)
                in_redirect = False  # past the operator, into the (glued) target or argv
        pos, state = resume, resume_state
    return [*out, "".join(seg)]


def _rm_wipe_targets(command: str) -> "set[str]":
    """The targets (``root`` / ``home``) a recursive-force ``rm`` in *command* wipes."""
    kinds: set[str] = set()
    for segment in _segments(command):
        words = _shell._shell_tokens(segment)
        while words:
            if ENV_ASSIGNMENT_RE.match(words[0]):
                words = words[1:]
                continue
            is_redirection, consumes_next = _shell._push_token_redirection(words[0])
            if is_redirection:
                # a leading redirection runs before the command; drop its word, and
                # its separate target word too when the operator did not glue one on
                words = words[1:]
                if consumes_next and words:
                    words = words[1:]
                continue
            break
        if not words or _shell._program_basename(words[0]) != "rm":
            continue
        recursive = force = done = False
        operands = []
        for arg in words[1:]:
            if done or not arg.startswith("-") or arg == "-":
                operands.append(arg)
            elif arg == "--":
                done = True
            elif arg.startswith("--"):
                recursive = recursive or arg == "--recursive"
                force = force or arg == "--force"
            else:
                recursive = recursive or "r" in arg or "R" in arg
                force = force or "f" in arg
        if recursive and force:
            kinds |= {kind for arg in operands if (kind := _target_kind(arg))}
    return kinds
