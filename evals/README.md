# Skill evaluations

Measured checks on bundled skills, one directory per skill.

A skill is a prompt, and a prompt cannot be reviewed for correctness by reading
it. These harnesses answer two questions a code review cannot:

1. **Does the skill load when trigger matching is on?** Word-overlap trigger
   matching is **opt-in**: `skills.max_triggered` defaults to `0`, which disables
   it entirely, and the default discovery route is the Available Skills index plus
   `skill_search` / `$skillname`. For an install that has turned it on
   (`max_triggered > 0`), a skill auto-injects only when one of its `triggers`
   phrases clears `_MIN_TRIGGER_OVERLAP` (0.7) — and beautiful guidance behind a
   trigger that never fires is invisible dead weight for that whole population.
   This is deterministic, so it is a CI gate.

2. **Does the skill change the answer?** Each prompt is answered twice, once with
   the skill body prepended and once bare, and both are graded against explicit
   per-case assertions. This needs a model, so it is a local command, not a gate.

What this does **not** cover is the default path. There, a skill is found through
its `description`, which the generic bundled-skill frontmatter test already pins.

## Layout

```
evals/<skill>/cases.json      prompts, expected audience/behaviour, assertions
evals/<skill>/run_evals.py    --check (deterministic) and --run (A/B)
evals/<skill>/fixtures/       inputs a --run needs, when it needs any (crew-setup)
evals/<skill>/iteration-N/    A/B outputs and grading, auto-incremented, gitignored
```

## Running

```bash
# Deterministic validation. No model, no tokens. What CI runs.
python3 evals/explain-for/run_evals.py --check -v

# The A/B measurement. Needs an agent CLI on PATH.
python3 evals/explain-for/run_evals.py --run

# One case, skill lane only
python3 evals/explain-for/run_evals.py --run --test=2 --with-skill-only
```

`--run` re-runs `--check` first and refuses to spend tokens on an inconsistent
case set. The CLI defaults to `kiro-cli chat --no-interactive --trust-tools=`;
override with `EXPLAIN_FOR_EVAL_CLI`.

## Adding a case

Append to the `cases` array:

```json
{
  "id": 13,
  "name": "explain-oauth-partner",
  "prompt": "Explain this to my wife: why login broke on her phone",
  "audience": "Partner",
  "expect_trigger": true,
  "assertions": [
    "Analogy comes from a shared daily routine, not from software",
    "Explains that the phone held an expired key",
    "Warm and patient in register",
    "No jargon -- no 'token', 'session', or 'refresh'"
  ]
}
```

`audience` must match a row label in the skill's own audience tables, and the
prompt must clear the trigger threshold — `--check` fails the case otherwise
rather than letting it silently measure nothing.

Set `expect_trigger: false` for a **control**: a prompt that contains explanation
vocabulary but is not an audience request. Those pin the upper bound on trigger
looseness, so a future widened trigger fails a test instead of quietly re-pitching
ordinary questions.

Do **not** reuse a prompt the skill spells out in its own "Worked shapes" section.
The skill body is injected ahead of the prompt, so a duplicated example is already
answered in the with-skill lane's context: the lane wins on recall and tells you
nothing about unseen phrasing. `--check` fails the case for this, so the two files
cannot silently converge as either one is edited.

Four assertions per case works well. Write them as things a grader can point at
in the text, not as tastes.

## crew-setup: persona evals of the first run

`evals/crew-setup/` measures a whole conversation instead of one answer: the
one-chat first run, from the privacy card to a kept job, as RFC
[one-chat first run](../docs/request-for-change/rfc-one-chat-first-run.md) §8
asks. Its `cases.json` holds trigger cases (checked like explain-for's, with the
same scoring code) and 6–10 personas. Each persona has a `persona` and an
`objective` in prose, a `script` of user replies, a click `policy` per card kind
(`accept`, `decline`, `ignore`, `preview-then-keep`, `preview-then-decline`; a
list applies per occurrence), and `rubric` lines graded from the run's record
alone: cards before the first kept job, minutes to a kept job, no card of a kind
after the user declined it, no request to paste a secret, no link the agent
invented, no unexpected `setup_card` refusal, a pasted token stored only as a
`secret://` reference, and no step offered in words when it has a card
(`no_prose_offer`, with at most one question beside the card in
`questions_beside_card`). `rubric_defaults` apply to every persona.

`no_prose_offer` reads the transcript turn by turn. A turn opens at a user
message or a gateway inject (the `[First run]` kickoff, a `[Setup card
result]`). Until a card of a kind first appears, a question in a turn that
offers that step ("Want me to bring Hermes over?", "Which forge is your repo
on?"), or a suggestion chip naming it in a turn with no card, is a prose-only
offer. The step patterns live in `cases.json` under `prose_offer`, with
examples `--check` holds them to. Persona 9 (`card-clicker`) exists to measure
it: it clicks every card and only ever answers "Go on.", so an offer made in
words is never rescued by the user.

The user side is **scripted, not simulated**: the same replies and clicks on
every run, so two iterations differ only by the agent. A model playing the user
from the persona text is a later step.

```bash
# Deterministic: schema, policies, rubric arguments, trigger cases, and the
# secret-request and prose-offer patterns against their own examples. What CI
# runs.
python3 evals/crew-setup/run_evals.py --check -v

# Real first runs with a real model (about ten minutes each). Needs the repo's
# .venv (the runner starts .venv/bin/kirocrew) and a signed-in kiro-cli.
python3 evals/crew-setup/run_evals.py --run            # every persona
python3 evals/crew-setup/run_evals.py --run --case 3   # one persona

# A rate, not an anecdote: the same persona six times, three gateways at once.
python3 evals/crew-setup/run_evals.py --run --case 9 --repeat 6 --parallel 3
```

With `--repeat`, each run is `iteration-N/<id>-<name>-rK/` and the table ends
with each rubric line's pass count over the graded repeats. Runs in parallel
share only the model; each still has its own homes and port.

`--run` gives every persona its own gateway: a fresh temp `KIROCREW_HOME` and
`KIRO_HOME`, a free port (never 5476), `KIROCREW_CLOUD_SIMULATE=1`, telemetry
off, AWS credential lookup pointed at empty files, a fixture Hermes home
(`fixtures/hermes/`, fictional) for the import and empty homes for every other
agent, and a throwaway git repository built from `fixtures/repo/commits.json`
for `{{REPO}}`. Before the gateway starts, every MCP server named in
`~/.kiro/settings/mcp.json` is declared disabled in the eval's data home,
because Kiro Crew merges those servers (mail, chat, documents) into every crew
home; after it starts, the run is refused if any crew agent spec still has an
enabled server Kiro Crew does not own. The names are read at run time and never
written to a record. `connect`, `service` and `channel` cards can only be
declined or ignored, since committing them would leave the sandbox (an OAuth
page, a system service, a real bot API). A held tool call, in the chat or from a
job's preview run, is allowed once only when it is read-only git, or a file read
confined to the throwaway repository or the eval crew's own skills (a skill's
references); everything else is rejected. The gateway
is always stopped at the end.

Each persona writes `iteration-N/<id>-<name>/record.json` (cards, transcript,
sends, approvals, timings, refusals, the secret scan) and `grading.json` (one
verdict per rubric line, with the evidence), and the run prints a table. A
failing line is a finding about the agent or the gateway, not a reason to relax
the rubric.

## A longer trigger is LOOSER, not tighter

Worth knowing before editing a skill's `triggers` line, because it reads backwards.
Matching scores `|trigger_words ∩ message_words| / |trigger_words|` against a `0.7`
floor — the threshold is a *fraction of the trigger*, so adding words buys slack:

| Trigger length | Words that must appear | Effect |
|---|---|---|
| 1-3 words | all of them | exact; the phrase means what it says |
| 4 words | any 3 | one word is optional, and you do not choose which |
| 5 words | any 4 | same, one slot free |

So `explain it to my` never actually required `my`: `{explain, it, to}` alone scores
0.75 and fires, which matched "can you explain it to me" — an ordinary request with
no audience in it at all. Collapsing it to the 3-word `explain to my` makes the
possessive mandatory again and drops that prompt to 0.67. `break this down for` had
the identical flaw (bare "break this down" scored 0.75) and became `break down for`.

The rule: if one word in a trigger is the part carrying the meaning, the trigger has
to be short enough that that word cannot be the one dropped. Control cases are how
you find out you got it wrong.
