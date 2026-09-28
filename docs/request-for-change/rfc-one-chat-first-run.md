---
title: One-chat first run — from one command to a delivered job in a single conversation
status: draft
author: zedmor
created: 2026-09-27
last-audited: 2026-09-27
audited-at: 20ed6a1dc7
revision: 3
doc-pr:
implementation-prs: []
tracking-issues: []
supersedes: []
superseded-by: []
---

# RFC: One-chat first run — from one command to a delivered job in a single conversation

- **Status.** Draft, revision 3, open for review. A working prototype of v1
  exists on the author's branch to make the review concrete; it lands only
  after this document is accepted. Checked at `20ed6a1dc7` (main, 2026-09-27);
  citations name files and symbols, not line numbers.
- **What changed from revision 1.** Revision 1 proposed the whole "self-building
  crew" in a single RFC. A product critique (Appendix C) split it into two bets:
  - **v1 (this RFC):** a first run that is one conversation, for developers and
    for people switching from another agent.
  - **North star (§10):** the crew finds its own permanent home, moves there,
    builds its own connectors and follows you to every device. This part goes
    to follow-up RFCs, each gated on evidence.
- **What changed in revision 3.** A team review (Appendix D) added the
  **parallel home**: in the Egg the user may pick "my AWS account", and the home
  is built in the background while the first run proceeds locally, so it never
  lengthens onboarding (§5.7, §6.8). Building the prototype also simplified the
  tool surface to one `setup_card` directive tool plus `setup_status` (§6.4).
  The same revision adds the **main chat** (§6.9) and folds in a reading of
  Muse's agent-side documentation and skills (Appendix F): hosted-assistant
  import (§5.3), a first week that teaches in small steps instead of a longer
  first run (§5.8), persona-driven first-run evals (§8) and the connector
  scaffold that §10.3 builds on.
- **Author.** Akim Akimov (zedmor)
- **Related documents.**
  - [rfc-crewmates-launch.md](rfc-crewmates-launch.md): screen 08, "Meet CrewMates", is an
    accepted onboarding decision this RFC touches (§6.2, Q5).
  - [rfc-update-architecture.md](rfc-update-architecture.md): start.sh must declare its install
    shape.
  - [rfc-host-credential-vending.md](rfc-host-credential-vending.md): the nearest existing design to
    credential surrogation.
  - [rfc-remote-instance-on-fargate.md](rfc-remote-instance-on-fargate.md) and
    [rfc-outbound-instance-transport](https://github.com/kirodotdev/KiroCrew/pull/13031)
    (open PR #13031): the north-star nest work builds on these.
  - Open PRs: #13888 (first-run harness picker), #11263 (onboarding branding
    seams) and #10162 (remote A2A subagents).

## 1. Summary

Today a new user meets five surfaces with five different UIs:

1. a terminal installer that stops at "Next steps";
2. a harness install and sign-in, done separately;
3. a terminal wizard or four dashboard chapters;
4. a Settings page for each channel;
5. a separate cloud wizard.

None of these knows what the others already asked, and the agent — the best
setup tool the product has — is locked out of all of them.

This RFC proposes a **one-chat first run**:

```
curl -fsSL https://download.crew.kiro.dev/start.sh | sh
```

It installs Kiro Crew through `cli.sh`'s signed path, gets a harness ready,
starts the gateway and opens a chat. The first-run desktop app lands in the
same chat. There, the agent:

- finds what you already have: a Hermes or OpenClaw agent, kiro-cli
  configuration, repositories;
- offers to bring it over;
- connects one developer service, such as GitHub;
- **runs a first job immediately, as a preview**, then schedules it: a morning
  dev brief of reviews waiting, red builds and today's calendar, a PR watch, or
  your imported crons, adapted;
- keeps itself running on this machine when you ask.

Name, language, timezone and tone are asked for in passing. Everything else is
learned from corrections, not from a questionnaire.

Three positions carry the design:

1. **The model proposes; deterministic UI commits.** Anything that changes
   security posture, touches a credential, or starts something that keeps
   running is an inline **card**. Core code renders the card from a stored
   payload, and the user's click commits it. The model's text never does.
2. **Value before infrastructure.** The first useful output comes from the
   user's own data, within the first session and inside ten minutes, not "the
   next morning". A permanent home is offered when the user feels the need for
   one, not before.
3. **Reuse, don't rebuild.** Almost every part already exists:
   - the signed wheel install;
   - onboarding import from Hermes, OpenClaw, Claude, Codex and Gemini;
   - `ask_question` cards;
   - MCP Apps render markers;
   - the connections mint flow and its curated registry (GitHub, Linear,
     GitLab, Atlassian, Sentry and others);
   - the vault, the cron service, the PR watch and the service installer.

   What is missing is the conversation that ties them together, a small set of
   governed tools the agent can drive, and three new pieces: a credential
   capture card, light SOUL.md/USER.md authoring, and pasted-secret redaction.

## 2. Motivation

### 2.1 What a new user goes through today

| Step | Today | Where |
|---|---|---|
| Install | Verifies a KMS-signed channel manifest, installs the wheel into a managed Python, prints three "Next steps" and exits. It installs no harness, runs no setup, starts no gateway and opens no browser. | `cli.sh` |
| Harness | Install kiro-cli from kiro.dev and run `kiro-cli login` in a terminal. Until then a full-screen gate blocks the dashboard; the gate detects the harness but never installs it. | `src/kiro_crew/kiro_prerequisite.py`, `website/src/components/KiroPrerequisiteGate.tsx` |
| First run | The terminal wizard `kirocrew setup` (plain `input()` prompts), or four dashboard chapters: Import, Privacy, Customize & tour, Meet CrewMates. | `src/kiro_crew/cli_setup.py`, `website/src/components/AgentImportFlow.tsx`, `website/src/components/PrivacyChapter.tsx`, `website/src/components/OnboardingFlow.tsx`, `website/src/components/MeetCrewmatesFlow.tsx` |
| Identity | A `bot_name` setting and two profile fields, `user_role` and `user_technical_level` (`codes`, `somewhat-technical`, `non-technical`). A per-member SOUL.md is read if present; nothing writes one. | `src/kiro_crew/config/sections.py`, `src/kiro_crew/member_essential_context.py` |
| Connectors | The Connections page, with one card per curated provider (29 in the registry, developer tools first: GitHub, Linear, GitLab, Atlassian, Sentry, Vercel, …). kiro-cli owns every OAuth grant. Google services need an OAuth app the operator registers. | `src/kiro_crew/connections/registry.json`, `website/src/pages/connections/ConnectionsPage.tsx`, [oauth-app-registration](../guides/oauth-app-registration/README.md) |
| Channels | Paste a bot token and a numeric user-ID allowlist into a Settings panel for each channel. | `website/src/pages/settings/` |
| Jobs | Crons exist and imported crons can be applied, but the first run proposes none and delivers nothing. | `src/kiro_crew/mcp_cron.py`, `src/kiro_crew/onboarding_import.py` |

### 2.2 Problems

1. **The installer stops before the product starts.** Tenet 3 promises
   "productive in 60 seconds". `cli.sh` ends by listing three commands to type.
2. **Setup is spread over five surfaces**, and none of them knows what the others
   asked.
3. **The agent is locked out of setup.** No MCP tool can add a connection, pair
   a channel or install the service. The agent can only send a Settings deep
   link.
4. **Nothing useful happens in the first session.** Onboarding ends at a
   configured, empty agent. The first value a user could receive — a job that
   reads their own repositories or inbox — is never proposed, and a scheduled
   job would not deliver until the next day anyway.
5. **The best first message is hidden.** `detect_sources` already finds Hermes,
   OpenClaw, Claude, Codex and Gemini setups on the machine. That discovery is
   buried in chapter one's import screen, when it could be the opening line.

### 2.3 Who this is for first

This is a choice, and the rest of the document follows from it.

| Segment | Fit | Why |
|---|---|---|
| **Developers who already run kiro-cli or another ACP harness** | **First** | The harness step is already done, so the whole first run could take a minute. The README positions Kiro Crew as "a persistent workspace for development work". The curated registry is developer tools. Their jobs are concrete: PR babysitting, CI watches, a morning dev brief. |
| **OpenClaw and Hermes users looking to switch** | **First** | These are large communities (OpenClaw has about 391K GitHub stars, Hermes about 249K), and `onboarding_import` already reads both. "I found your Hermes setup — six jobs and a memory file. Bring them over?" is the strongest opening a first run can have. |
| Home-lab and self-host enthusiasts | Second | A crew on your own Mac mini can reach your LAN, repositories, Home Assistant and NAS, which a hosted VM cannot. They are the natural audience for the north-star nest (§10). |
| Non-technical users | Later | Meta's Muse, Google's CC and xAI's Grok Bot now serve them free, with no install and on their phones. `curl … \| sh` is not their way in. The signed desktop app, which already bundles a pinned kiro-cli, is. Because the first run is built from cards rather than shell commands, that path stays open at no extra cost. |

### 2.4 What Muse showed, taken as hypotheses

Meta launched Muse on 2026-09-08. It gives each user a dedicated cloud VM and
reaches first chat in under a minute. Condensed research is in Appendix A. The
lessons below come from Meta's public material plus one author session
(Appendix B). They are hypotheses, not findings:

- **Put the name first, then one connector at a time, each followed right away
  by "here's what I can do now".** Users like naming the agent, and a
  capability reveal turns a connection into a reason to keep going.
- **Use deterministic UI where it matters.** Approval cards are structured
  records (agent name, action, target, full payload, Allow/Deny), and they go
  from the permission authority to the client, *not through the conversation*.
  Secrets go through a separate capture UI, never the chat.
- **Ask in the chat, not in Settings.** Muse's worst moment in the author's
  session was telling the user to find a permission row that existed only in
  the web app.
- **Adapt imported jobs; don't copy them.** Muse re-pointed Hermes crons to a
  new delivery channel, merged two of them into an existing watch, dropped a
  Mac-only job and asked which language to use.

Muse's minute is not a like-for-like target. It starts after an app-store
install and a Meta sign-in, and it ends with seven accounts already connected.
Our comparable bar is **time to first value**, not time to first chat.

## 3. Goals

- **G1. One command to the first reply.** From a clean macOS or Linux machine
  (and Windows through start.ps1), with a harness already signed in, the first
  agent message arrives in ≤ 90 s of machine time at p50, and no second command
  is typed. When a harness sign-in is needed, its human time is reported
  separately rather than budgeted.
- **G2. First value in the first session.** Within ten minutes at p50, the user
  sees an output grounded in their own data: a preview brief, a PR or CI
  summary, or an import summary.
- **G3. One job the user keeps.** The first run ends with at least one job that
  has **delivered** something and is still enabled at day 14. Creating a job is
  not enough.
- **G4. Import first.** When `detect_sources` finds another agent, bringing it
  over is the opening offer, and its crons arrive *adapted*.
- **G5. Keep running when asked.** "Stay on when I close the browser" is one card
  (`kirocrew service install`).
- **G6. Setup is always one sentence away.** "Connect Linear", "pair Telegram"
  and "watch this repo" work from any later chat, through the same tools.
- **G7. Nothing gets weaker.** No default is loosened, the sandbox scope is not
  widened, and no security-relevant change is committed by the model alone.
- **G8. A home without waiting.** A user who picks "my AWS account" in the Egg
  gets a permanent home built in the background. Choosing it adds no time to
  G1 or G2: the chat starts locally at once, and the home is offered to move
  into only when it is ready.

## 4. Non-goals (v1)

These are north-star work (§10), each with its own follow-up RFC:

- a home on a machine the user brings over SSH, Fargate, and live migration of
  a running crew (the EC2 home in the user's own AWS account is in v1 as the
  opt-in parallel home, §6.8);
- agent-built connectors, which need an egress authority first;
- personalised desktop builds, a personalised PWA, and native mobile apps.

These are out of scope entirely:

- a hosted Kiro Crew service;
- a new LLM provider or harness (`agent.provider` stays `acp`);
- a new built-in app (the `no-new-builtin-apps` rule holds);
- designing the first run around non-technical users (§2.3);
- removing `cli.sh`, `install.sh`, `kirocrew setup` or the chapters;
- changing defaults for the sandbox, approval mode or governance.

## 5. The experience

```
 Egg ──────▶ Home ──────▶ Hello ──────▶ Bring & connect ──────▶ Preview now ──────▶ Keep it ──────▶ Stay on
 no model    where the     name, lang,   import found agents;    run the job once   schedule it;    service card
 install     crew lives    tone; what    connect one dev         on your data       adapted crons   (when asked or
 harness     (a card; the  I found       service                                                    when it hurts)
 privacy     build runs in the background)
```

Only the Egg is fixed. After that, the agent *recommends* the order. The user
can skip, reorder or stop, and a later chat picks up where this one left off.

### 5.1 Egg: the part with no model

start.sh runs these steps and asks nothing: every choice after the command is
made in the web chat. The one terminal step left is the harness's own sign-in on
a signed-out machine, which runs without a yes/no because the chat cannot start
without it (it opens the browser, or prints a device code on a headless host).
Where the crew lives is a step of its own in the chat, right after privacy (§5.7);
`--home here|cloud|later` lets a script answer ahead of time.

1. **Install as `cli.sh` does.** It uses the same signed manifest, the same
   pinned trust root and the same managed Python (§6.1).
2. **Make a harness ready.**
   - If kiro-cli or another selectable harness is present and signed in, it is
     used as is.
   - Otherwise start.sh offers the pinned kiro-cli that the desktop build
     already verifies (`packaging/kiro-cli-version`,
     `packaging/kiro-cli-sha256`), and whether it may do so is Q2. It then runs
     the harness's own device-code sign-in, which works over SSH and on
     headless hosts.
3. **Start the gateway.** It runs in the foreground; `--service` also installs
   the service.
4. **Open the chat.** start.sh mints a one-time sign-in URL (the `kirocrew token`
   path in `src/kiro_crew/cli_server.py`) and opens the browser on the first-run
   session. On a headless host it prints the URL and a QR code, so the user can
   finish on another device.
5. **Show the privacy disclosure as the first card**, before the first model
   turn. It uses the same strings and the same `privacy_acked` flag as
   `PrivacyChapter.tsx`. A disclosure is not the model's to paraphrase.

The desktop app's first run skips steps 1–3, because it already bundles the
backend and kiro-cli, and lands in the same session.

### 5.2 Hello

The agent's first message says what it is, what it will ask approval for, and
what it found. For example:

> I found a Hermes agent here, with six scheduled jobs and a memory file, and
> kiro-cli is signed in. Want me to bring Hermes over? Also, what should I be
> called? Here are three ideas.

It points in one sentence to the home step already on screen (§5.7) and does not
ask it again in prose. The prototype first asked it only in that sentence; a tester
never noticed it next to the import card, so it became a card of its own.

It asks four things in passing: a name, a reply language, the timezone
(pre-filled from the OS) and the tone. It asks whether the user writes code and
maps the answer onto `user_technical_level`. It asks nothing else yet.
Proactivity, interruption limits and quiet hours start from defaults, which
SOUL.md records and the user adjusts later ("stop pinging me after 7pm").

### 5.3 Bring and connect

- **Import.** The agent calls `import_scan` and shows a preview card of what
  would come over. Imported crons are adapted: delivery is re-pointed,
  duplicates are merged into existing watches, and host-specific jobs are
  dropped with the reason stated.
- **From a hosted assistant.** A switcher from ChatGPT, Claude or Gemini has no
  files to scan. The agent hands over a short prompt to paste there; the user
  reviews the answer, removes what they would rather not share and pastes the
  rest back. The agent condenses it into a `soul` card for USER.md, so the user
  reviews it a second time before anything is written. The pasted text is
  material, not instructions, and passes through pasted-secret capture (§6.6)
  like any message.
- **One developer service.** GitHub by default, or Linear, GitLab, Atlassian
  or Sentry, from the curated registry, through a **connect card** (the existing
  mint consent flow). As soon as the service is connected, the agent says what
  it can now do, in concrete terms.
- **One channel, optionally.** Telegram, Slack or Discord through a credential
  card for the bot token, plus a `/pair 4821` message that allowlists the
  user's own ID without asking them to look it up.

### 5.4 Preview now, then keep it

The agent proposes one to three jobs from what it found: a morning dev brief
(reviews waiting on you, red builds, today's calendar if one is connected), a
PR watch on a repository (the existing babysit-pr-watch), or an imported job.

**Each job runs once, immediately, as a preview.** The cron card shows that
output, the schedule and the delivery target, and offers **Keep it**. The user
keeps what was useful. Value arrives in the first session instead of sixteen
hours later.

### 5.5 Stay on

Offered once there is a job worth keeping, or when a check is missed because
the laptop slept ("I missed two checks while your laptop was asleep — want me
to stay on?"). The card runs `kirocrew service install`
(`src/kiro_crew/service/controller.py`). On Linux that needs sudo, so the card
says so and hands off to a terminal prompt. A permanent home on another machine
is north-star work (§10.1).

### 5.6 Showing that the crew builds itself, within v1's limits

The thesis that "the crew builds itself" does not have to wait for agent-built
connectors. Three things the agent can already do in chat demonstrate it with
no new credentials and no new egress:

- writing a **skill** for a recurring task (the `crystallize` builtin skill
  stages it for approval);
- writing a **scheduled job**;
- writing a small **dashboard app** for the user's data.

The first run offers one of these once a job has been kept ("Want a page that
shows this every morning?").

### 5.7 The home, built while you talk

Right after privacy, every first run shows a deterministic **"Where should your
crew live?"** card: stay on this machine (one click), or a home in the cloud in the
owner's AWS account at the stated monthly cost. When the AWS CLI is signed in it
names the account's last four digits and the region and builds on one click; when
it is not, the same card walks the owner through it first: install the AWS CLI if
it is missing, create an AWS account if they have none, sign in (§6.8 rule 2), and
the agent guides them through those steps in the chat. Once they press Build, the
card stays in the chat and shows the build's progress.
Nothing waits on it: name, import, connect and the preview job all happen
locally meanwhile. When the home is healthy the card turns into **Move in**:
one click hands the crew over (memory, schedules, settings, SOUL.md and USER.md,
and this chat) and the same conversation continues from the home. If the user
closes the laptop mid-build, the build carries on; the card picks up where it
was on the next start.

### 5.8 The first week: small steps, not a longer first run

The first run stops at the first kept job; everything it did not cover waits.
Muse does the same: its feature tour runs as a scheduled job over the first
days, not inside the first conversation. For Kiro Crew:

- The gateway posts at most one short note a day in the main chat for the
  first seven days, choosing the most useful thing still unset (a connection,
  staying on, a channel, a second job, SOUL.md, a skill written from a repeated
  request). The note is a fixed system notice, not a model turn, so it costs no
  quota. It does not post on a day the user has not used the main chat, and only
  in the daytime.
- The note suggests and asks; it never raises a card. A card needs a turn a
  person started (SC8), so the user's reply is what brings the card.
- The user can stop it with a word ("no more tips"), and it stops by itself
  after two notes in a row get no reply and at the end of the week. It is a
  gateway task, not a scheduled job, so it never appears in the job list and
  there is nothing to clean up.

## 6. Design

### 6.1 start.sh and the installer

start.sh is not a second installer. It shares `cli.sh`'s trust root: the pinned
manifest key, the signed-manifest verification and the managed Python. Two
copies of a trust root drift apart. Q1 chooses between two shapes:

- generate start.sh from `cli.sh` at publish time;
- add `--first-run` to `cli.sh` and make start.sh a thin wrapper, uploaded by the
  same `publish-installer.yml` workflow.

The modes:

| Mode | Invoked as | Does |
|---|---|---|
| interactive | `start.sh` | install → harness → gateway → browser → first-run session |
| plain | `cli.sh` | exactly what it does today, byte for byte |

A PowerShell counterpart, start.ps1, sits beside `install.ps1`. Windows has no
`cli.sh`, so its trust root is the Authenticode signature of the desktop
installer and the publisher the desktop updater already pins; the prototype
installs that signed installer silently and then runs its bundled
`kirocrew start`. start.sh
declares its install shape for
[rfc-update-architecture.md](rfc-update-architecture.md): the update engine
treats it as `wheel`, and the heartbeat reports it under a distinct
install-path value (§8).

### 6.2 The first-run session and its state

- The gateway creates the first-run session on first start, when
  `dashboard.onboarded` is false and no session exists. An install that has
  already been onboarded never gets one.
- First-run state (stages done, choices, what import found) is kept in a state
  file under the data home, not in `config.json`.
- **First-run state gates nothing (SC6).** It changes what the agent suggests
  next and nothing else. `privacy_acked` remains a config flag that only the
  privacy card's handler writes.
- `/onboarding` still opens the four chapters. For installs that went through
  the first run, the chapters do not open on their own. That is a product-shape
  change, and whether rfc-crewmates-launch already covers it or it needs its
  own recorded decision is Q5.
- The first run names the *primary* agent (`bot_name`). Meet CrewMates (screen
  08) still creates *additional* crewmates, and the first run can offer "want a
  teammate for X?" through the same `POST /api/agents`.

### 6.3 Cards

A card is a **server-side pending action with a rendered face**:

1. A setup tool call creates a pending action: id, session owner, payload and
   payload hash.
2. The tool returns "awaiting user".
3. The dashboard renders the card from the stored payload, never from model
   text.
4. A click posts to a gateway endpoint bound to (owner, card id, payload hash).
5. The handler commits the change.
6. The model's next tool result says "approved" or "declined", plus the outcome.

The model cannot forge a click. A changed payload has a different hash, so it
becomes a different card. The proposal travels the way `ask_question`'s card
does: the tool returns a **session directive** (`src/kiro_crew/session_directive.py`),
which the session's own consumer applies after the forgery gate has checked the
call's out-of-band identity. The applier builds the card's payload
gateway-side, stores it durably (`src/kiro_crew/setup_cards.py`), appends an
inline transcript row whose meta carries only the card id, and sends an
owner-only websocket event. The browser renders the card from
`GET /api/setup/cards/{id}`, never from row text, and the only commit path is
`POST /api/setup/cards/{id}/decide` carrying the hash the owner was shown. A
decided card is reported back to the agent as a `[Setup card result]` envelope
turn. A card never replaces a PreToolUse approval; it sits on top of one.

| Card | For | Commits |
|---|---|---|
| choice | name, language, tone, which job | a value returned to the model |
| preview | SOUL.md, USER.md, profile, import | a file or config write, through the config appliers |
| connect | a curated provider | the connections mint consent flow (`src/kiro_crew/connections/mint.py`) |
| credential capture | a bot token or API key | a vault entry; the model receives only `secret://NAME` |
| pair | a channel | a one-time pairing code |
| cron | a job with its preview output | a job through the existing cron service |
| service | stay on | `kirocrew service install` |

**Guardrails.**

- **Card budget.** The first run shows at most eight cards before the first kept
  job. Every card beyond the first three must justify itself against the
  funnel data (§8).
- **Stakes look different.** Credential, service and connect cards look
  different from choice cards and never auto-advance. Claude Code users approve
  about 93% of permission prompts, so high-stakes cards must not look like
  "Next".
- **Classic escape.** Every card offers "use classic setup", which opens the
  matching chapter or Settings panel.
- **Stall watchdog.** If a first-run turn makes no progress for 90 seconds, or
  the kickoff turn ends without a reply, the gateway posts a deterministic
  notice with Try again and "use classic setup". It reads the existing
  session-health classifier, so a turn waiting on an approval, a question or a
  sub-agent never counts as stalled.

### 6.4 Setup tools and the `crew-setup` skill

One builtin skill, `crew-setup`, lives under `src/kiro_crew/builtin_skills/`. The
first-run session starts with it loaded, and in other sessions it loads on
intent. The tools follow the existing rules: they are stateless, the proposal tool is a
session directive, and every card is governed by one new `SCOPE_CATALOG` row,
`capabilities.setup`, whose inner `kinds` ruleset lets a fleet refuse some kinds
(a data change, not an evaluator change). Cron cards also pass the existing
`capabilities.cron` gate.

| Tool | Does | Notes |
|---|---|---|
| `setup_card` | proposes one card; `kind` is one of profile, soul, import, connect, credential, channel, cron, service | one tool rather than one per kind: every core tool's schema rides on every request, and the kinds share one lifecycle |
| `setup_status` | reads this session's cards and the first-run stages | lets the agent verify an outcome instead of trusting chat text |

Cards are a dashboard surface, like `ask_question`, so neither tool has a CLI
twin; the CLI already has a command for each underlying action (`kirocrew cron`,
`kirocrew service install`, the vault commands).

**Who may propose (SC8).** A setup card may be raised only in a turn a person
started: a typed message, or a turn that exists because the owner clicked a
card. The first-run kickoff is such a turn (it follows the owner's click on the
privacy card), and so is every `[Setup card result]` turn. A turn started by a
cron, a watch, an injected event or a sub-agent is refused, in the first-run
session as anywhere else. Otherwise an email, a web page or an imported memory
file could steer the agent into proposing "connect this" to a tired user — the
lethal trifecta with one fatigued human as the only defence. The first-run state
file plays no part in this decision (SC6).

### 6.5 SOUL.md and USER.md, kept light

- **SOUL.md** holds name, voice, reply language, defaults for proactivity and
  quiet hours, and anything the agent must never do. **USER.md** holds what the
  agent knows about the user. It supersedes the two-field `[USER PROFILE]` block
  but still carries `user_role` and `user_technical_level`.
- **Both are small by design.** Each is capped (4 KB is proposed) and reaches
  the model through the context-block path
  ([context-management](../architecture/context-management.md)).
- **They grow from corrections.** When the user says "shorter, please" or "no
  messages on weekends", the agent proposes a one-line SOUL.md diff through a
  preview card.
- **Writes happen only through a preview card, the user's own edits, or a
  Settings editor.** An injected instruction that writes itself into SOUL.md
  would persist forever, so whether SOUL.md should be read-only to in-sandbox
  code is Q3. That would be a new seal, and seals are the operator's decision.
- **Imported souls stay data.** `onboarding_import` already drops the persona
  role from a foreign SOUL.md or USER.md. The first run shows the imported text
  as a *suggestion* inside the preview card.
- **Compatibility is a feature.** SOUL.md and USER.md follow the OpenClaw shape,
  which is exactly what makes switching cheap.

### 6.6 Secrets that users paste anyway

Users will paste tokens into the chat whatever the card says. When a user
message contains a value that matches a known credential shape — the same
detectors the log redaction uses (`src/kiro_crew/log_redaction.py`) — the
gateway does three things before the turn reaches the model:

1. moves the value into the vault under a proposed name;
2. replaces it in the transcript with `secret://NAME`;
3. posts a deterministic notice: "That looked like a GitHub token. I stored it
   in the vault and removed it from this chat."

This is SC2's missing half.

### 6.7 Quota awareness

A long first run followed by daily briefings can use up a free harness tier
within a week, and Kiro Free is 50 credits. To prevent that:

- the first run's background work (import summarising, preview generation) runs
  under the `background` role of `agent.role_models`, which resolves through
  `acp.client.resolve_usable_model` and never through a hardcoded model id;
- jobs proposed by the first run carry deterministic pre-filters, such as
  "skip the model when nothing changed";
- when the harness reports that its quota is exhausted mid-run (the structural
  `usage_limit` error, never message text), a deterministic notice explains what
  happened and what still works, and no new card is proposed until a turn
  succeeds again.

Phase 0 measures the real burn (test 1, §9).

### 6.8 The home, built in the background

The parallel home turns the north star's nest into a v1 path without making the
first run any longer. Five rules shape it:

1. **The first run never waits for it.** The chat starts locally at once. The
   build is a background job, observed through a status file under the data
   home, the way the embedding models already download without interrupting.
2. **Money and credentials are the user's, and the user says yes.** Before the
   first turn the gateway asks the AWS CLI who it signs in as (one read-only
   `sts get-caller-identity`, `src/kiro_crew/cloud/local_signin.py`); a signed-in
   machine is offered a home in the Hello, which names the account's last four
   digits, the profile's region and the monthly estimate. Nothing is asked in the
   terminal. Without a sign-in, the home card offers the AWS CLI's own browser
   sign-in, `aws login` (OAuth 2.0 with PKCE, AWS CLI 2.32+, short-lived
   credentials refreshed for up to 12 hours): its "Sign in to AWS" button runs
   `aws login` as a child of the gateway, which opens the sign-in page in the
   owner's browser, and the card turns back into "Build my home" once AWS answers
   for the profile. That works only when the browser and the gateway share a
   machine, because the CLI takes the sign-in's redirect on this machine's
   loopback; a remote or headless gateway's card shows `aws login --remote` to run
   in a terminal there instead. IAM Identity Center (`aws sso login`) is only
   named for users who already sign in that way: it needs an organization, and
   creating one ends a new account's Free plan. Kiro Crew never stores or sees
   AWS credentials (SC4); the aws CLI resolves them from the user's profile
   exactly as `src/kiro_crew/cloud/` does today. The region comes from the
   profile, because accounts made through AWS's newest sign-up are pinned to one
   region by country. The home card states the instance size, the region and an
   estimated monthly cost, and building needs the owner's click on it. A user
   without an AWS account asks for a home and the card walks them through the
   signup: "Create an AWS account" opens AWS's own sign-up page in a new tab
   (never framed or proxied, so Kiro Crew collects nothing), and when this
   machine's Kiro sign-in is AWS Builder ID it is the Builder ID sign-up, with
   no new password. The card then waits, untimed, for "I've created it — sign
   in", which runs the sign-in above, and says when the AWS CLI 2.32+ it needs
   is missing, linking AWS's install page. Since September 2026 that signup
   takes a Google, GitHub or Apple login and, for most people, no card, but it
   cannot be automated (Q9). The Hello does not offer a home to a machine with
   no AWS sign-in yet: new signup accounts are pinned to one region by country,
   which the offer would have to name first.
3. **One provider seam.** The build goes through the existing
   `RemoteProvisioner` seam (`src/kiro_crew/platform/interfaces.py`). The public
   build's provider is "your own AWS account" on the existing launch engine
   (`run_launch` in `src/kiro_crew/cloud/launch_job.py`, the CloudFormation
   template with SSM access, an encrypted root volume, IMDSv2 and no inbound
   port 22). A managed-hosting provider — pre-provisioned capacity, billed as
   credits, no AWS account at all — plugs into the same seam later without any
   change to the chat (§10.7).
4. **Moving in is a handoff.** When the home is healthy, the home card offers
   Move in: a portability export (`src/kiro_crew/portability.py`), an import on
   the home over the instance transport, and a transfer of the first-run
   session (`src/kiro_crew/dashboard/session_transfer.py`), all over the
   Instances tunnel the launch registered (`src/kiro_crew/dashboard/setup_move_in.py`).
   Credentials are not copied: the result names the secrets to enter again and
   the connections to grant again on the home, and doing the home first makes
   that rare. The local copy is kept: this chat stays as it was, and its
   schedules are switched off, not deleted. Crons are owned by exactly one crew
   at every moment (SC5): the local copies go off before the archive reaches the
   home and come back on if the home does not confirm it. The contract is in
   [first-run](../system-specs/modules/first-run.md#moving-in).

5. **Signing the home in: its own sign-in, one click.** The prototype's home
   signs in to Kiro with its own device code, which a person must approve while
   the build is running; in the recorded demo nobody did, so the home was ready
   but its agent could not answer. The home keeps its OWN sign-in, and the
   approval is made one click:
   - **Decision (2026-09-28, the user).** Own sign-in with one click, not a copy
     of this computer's token. Copying puts one long-lived refresh token on two
     machines, and Kiro's refresh answer may rotate it (`refreshToken?` in
     [kas-auth](../system-specs/modules/kas-auth.md#refresh)); kiro-cli
     serializes refreshes with a cross-process lock for that reason, so two
     machines refreshing one copy can sign the local kiro-cli out. Each machine
     keeping its own session avoids that entirely.
   - **One click.** kiro-cli on the home prints the verification page with the
     user code already in it (`verification_uri_complete`), so a browser already
     signed in to Kiro needs one confirmation. When the owner pressed Build from
     a browser on the same machine as the gateway, the gateway opens that page in
     it once, the moment the build reaches the sign-in; otherwise the link and
     code stay on the home card. Either way the chat gets one system notice that
     the home waits for that click. The contract is in
     [first-run](../system-specs/modules/first-run.md#the-homes-kiro-sign-in).
   - **Later: a token copy, as a spike.** Carrying this computer's sign-in to the
     home stays a possible follow-up, behind a spike that runs both machines on
     one copied token for a day per identity type (Builder ID, social, Identity
     Center) and shows rotation does not sign either one out (Q15).
   - **Sign-out.** Signing out here does not sign the home out, and signing the
     home out does not sign this computer out.

Plain words, honest numbers: the non-technical path says "a home in the cloud"
rather than naming instance types, but the cost card always names who bills the
user and roughly how much. A smaller "starter" size than today's cheapest tier
is measured before it is offered (Q8).

A simulated launch engine walks the same progress steps without AWS, so the
experience can be reviewed and tested before anyone spends money; it is labelled
as simulated wherever it appears.

### 6.9 The main chat: where the user spends 90% of the time

Muse's strongest idea is not its install; it is that the app is one chat. Kiro
Crew keeps its many sessions, crews and apps, but gives the user one place to
run all of them from.

- **The first run graduates into it.** The hatching phases are Egg → first run →
  **main**. When the first job is kept, the gateway posts a deterministic notice
  ("Setup is done — this is your main chat"), renames the session after the
  agent (`bot_name`), keeps it pinned first, and records it as the main chat. An
  install that never had a first run can make any chat its main chat later.
- **It is where the product opens.** `kirocrew start`, the desktop app and the
  dashboard land on the main chat when no other chat is asked for.
- **It can see everything.** Each turn in the main chat carries a compact
  `[CREW OVERVIEW]` block, built by the gateway: other chats that are running or
  waiting on the user, setup cards open anywhere, the next jobs due, and the
  home's state. It is information about the user's own sessions, the same the
  existing `list_sessions` tool already returns, so it widens nothing; it only
  saves the agent a lookup, and it stays small (bounded, newest first).
- **It can control everything, with the tools that already exist.** Session
  control (`session_create`, `session_send`, `session_read_message`,
  `session_stop`, `session_close`), sub-agents (`spawn_run`), jobs
  (`cron_*`) and setup cards. The `crew-setup` skill teaches the delegation
  habit: long work goes to its own session or a sub-agent, and the main chat
  reports back, so the main chat stays responsive and short.
- **Found while building the prototype: the default agent cannot hand off.** The
  session tools come from the `kirocrew-dashboard` MCP server, which is opt-in
  (`src/kiro_crew/agent.py`, `_MANAGED_MCP_SERVERS`); the default agent's
  spec does not mount it, so a first-run chat asked to "do it in its own chat"
  answers that session tools are not available. The main chat needs that server
  mounted, the way the conductors mount it: `session_create` and
  `session_read_message` granted, `session_send` and `session_stop` left to the
  approval gate. The prototype creates the first-run chat on such a spec,
  `kirocrew-main` (Q13).
- **Handing off is the user's choice.** Muse's skills offer a dedicated chat
  once, create it only after the user agrees, never ask again for that task,
  never hand off from a chat that is not the main one, and send the new chat a
  self-contained brief instead of the raw history. `crew-setup` carries the
  same rules.
- **Found in a demo: "I'll post the findings here" was a promise nobody could
  keep.** Nothing told the main chat when a chat it handed work to finished, so
  the user had to ask. The gateway now posts one deterministic notice in the
  main chat when such a chat ends a turn and is idle ("… finished. Ask me here
  for what it found."), with Open and Ask for the result, or, when the turn
  ended on an error with no reply, a warning with Open only. A turn someone
  stopped posts nothing. A notice due while the main chat is busy waits for its
  turn or plan to end and survives a restart. It is not a model turn, so it
  costs no quota and raises no card (SC8); the user's question is the turn that
  reads the result (MC.9).
- **The main chat stays.** In Muse it cannot be deleted; side chats can be
  archived. Kiro Crew's agent never closes it (a skill rule); whether the
  dashboard should refuse to delete it is Q11.
- **Channels keep their own chats.** A Slack or Discord conversation stays in
  its own session and is never mirrored into the main chat; the overview lists
  it like any other chat.
- **Controls elsewhere draft into the main chat.** Muse's "Edit" on a
  scheduled task and "Add subgoal" on a goal do not open forms; they draft a
  chat message for the user to send. The same pattern lets the jobs page, the
  sessions list and the connections page hand a change to the main chat without
  a new form, and the change then goes through the usual card (a follow-up UI
  item, MC.6).
- **What "main" is not.** It is not a permission. The main-chat marker lives in
  the first-run state file (presentation only, SC6): it decides where the
  product opens and whether the overview is attached, never what a turn may do.
  Every cross-session action still goes through the same governed tools and
  their own gates (`agent.session_control`, SC8 for setup cards).

## 7. Security model

The full mapping of Muse's architecture onto Kiro Crew is in Appendix A. It
shows where we match (the permission authority lives outside the model; the
harness is confined; credentials sit outside the agent's reach) and where we
have no equivalent yet (an egress chokepoint, TLS-inspecting approvals,
credential insertion at egress). v1 adds no new reach, so the missing egress
authority does not block it. It does block agent-built connectors (§10.3).

**Invariants.** Each needs a test before its phase exits:

- **SC1.** No setup tool commits a change without a card click bound to (owner,
  card id, payload hash).
- **SC2.** A secret entered in a credential card, or pasted and caught (§6.6),
  never appears in the transcript, a tool result, a log line or SEL detail.
- **SC3.** No setup tool writes a keystone file (`security_policy.json`,
  `profiles/`, `admission_policy.json`, `computer_use.json`) or changes the
  sandbox mode or approval mode. For those it can only link to Settings.
- **SC6.** First-run state gates nothing.
- **SC8.** Setup cards are raised only in turns a person started — a typed
  message, or a turn that exists because the owner clicked a card. A cron, a
  watch, an injected event or a sub-agent cannot put a setup decision in front
  of the user, in the first-run session or anywhere else.
- **SC4** (parallel home). The model never sees AWS credentials, and Kiro Crew
  never stores them; the aws CLI's own profile does.
- **SC5** (parallel home). A cron runs on exactly one crew at a time, including
  during a move-in.

(SC7, host-key pinning for a machine the user brings over SSH, stays with that
north-star work in §10.)

## 8. Measuring it

| Metric | Definition | Initial target (recalibrate after Phase 0) |
|---|---|---|
| Time to first reply | install start → first agent token, machine time, harness already signed in | p50 ≤ 90 s |
| Time to first value | install start → first output grounded in the user's own data | p50 ≤ 10 min |
| First-run funnel | Egg → privacy → first turn → import or connect → preview → job kept | drop-off reported per stage |
| **Activation** | within 72 h: at least one job delivered, the user engaged with it, and it is still enabled | ≥ 40% of first runs |
| Job survival | first-run jobs still enabled at D14 | ≥ 60% |
| Crew survival D1/D7/D30 | the instance still sends its daily heartbeat | the start.sh cohort beats the `cli.sh` cohort |
| Card load | cards per first run; abandonment per card kind | ≤ 8; watch the worst |
| Quota | crews that hit the harness limit in week 1 | < 10% |
| Home cost to onboarding | time to first reply and to first value, with vs without the cloud-home choice | no difference beyond noise |
| Home readiness | build start → home healthy | ready before the first job is kept, at p50 |

**No new telemetry fields.** The daily heartbeat already reports an install path
from a closed set (`src/kiro_crew/beacon.py`). start.sh gets its own value in
that set, and cohort survival becomes computable from data already collected.
Everything else is a local, user-visible first-run timeline, which the user may
choose to share, plus recruited test sessions.

**Persona evals before people.** Muse ships evaluation scenarios next to its
skills: a persona ("a first-time user, warm but impatient, taps to connect
accounts when prompted"), an objective, a mocked world and a rubric line
saying what passes. The first run gets the same treatment before each release:
a model plays the user from a persona file against a gateway on the simulated
engine, and a rubric checks what is checkable: cards before the first kept job,
minutes to the first preview, no card outside a person-started turn, no request
to paste a secret, no invented link. The Playwright driver used for the
prototype's demos is the start of it. Evals stand in for neither Phase 0's
interviews nor the moderated sessions; they catch regressions between them.

**Kill criteria.** After four weeks on Stable with n ≥ 30 first runs, v1 is
rethought if any of these hold:

- the median first run takes more than 12 minutes;
- activation is less than 10 points above the chapters cohort;
- D7 crew survival is no better than the `cli.sh` cohort.

## 9. Plan

Each phase has an **appetite**, a fixed time budget; scope shrinks to fit the
budget, never the other way round. A phase that overruns stops and is
reshaped, not extended.

| Phase | Appetite | Ships | Exit | Depends on |
|---|---|---|---|---|
| **0 Discover** | 2 weeks | The four tests below; the baseline funnel today on clean Ubuntu 24.04, macOS 15 and Windows 11; the choice of the v1 connector family | Numbers recorded here. Test 1 passes or its fix is scoped. | — |
| **1 Egg** | 2 weeks | start.sh on `cli.sh`'s trust root and start.ps1 on the signed desktop installer's; the harness step; gateway, one-time URL, browser or QR; privacy card first; the desktop first run lands in the session; the heartbeat install-path value | G1 on the three clean machines; `cli.sh` unchanged | 0; Q1, Q2 |
| **2 First run v1** | 6 weeks | Cards with guardrails; the first-run session and state; `crew-setup`; the §6.4 tools; import first; one connector family; one channel with `/pair`; preview-now jobs; the service card; pasted-secret redaction; quota handling | SC1, SC2, SC3, SC6 and SC8 tested; G2–G5 met in moderated sessions (n ≥ 10) | 1; Q3, Q4, Q5 |
| **2b Parallel home** | 4 weeks | The Egg's home choice; AWS CLI sign-in and the cost consent in the terminal; the background build through the provisioner seam with a status file; the home card; Move in (export, import, session transfer); the simulated engine | SC4 and SC5 tested; choosing the home adds nothing to G1/G2 at p50; a move-in keeps the chat and its transcript | 1 (can run beside 2); Q8, Q9 |
| **2c Main chat** | 2 weeks | Graduation from the first run; the landing target; the `[CREW OVERVIEW]` block; the delegation section of `crew-setup`; a "make this my main chat" affordance for existing installs | The first run ends as the main chat; the overview stays under its cap; opening the dashboard with no session named lands on it | 2 |
| **2d First week** | 2 weeks | The §5.8 note job; hosted-assistant import (§5.3); the persona evals (§8) wired to the simulated engine | The note job never raises a card and stops on request; the evals run on every release candidate | 2c |
| **3 Learn** | 4 weeks on Stable | Nothing new: measure against §8 | Kill criteria checked; the north-star gates (§10) read | 2 |

**Phase 0 tests** (riskiest first):

| # | Assumption | Cheapest test | Kill or pivot signal |
|---|---|---|---|
| 1 | The first-week loop fits a free harness tier | Script a first run on a fresh Kiro Free account, then run 7 days of brief plus one watch | > 35 of 50 credits in week 1: fix the burn first |
| 2 | Arrivals want dev-signal jobs first | 12 Mom-Test interviews (4 kiro-cli users, 4 OpenClaw/Hermes users, 4 who installed Kiro Crew and stopped); a fake-door split on the landing page ("your dev work" / "your life") | Dev jobs rank below life jobs among developers: revisit §2.3 |
| 3 | A conversational setup beats the chapters | Wizard of Oz: a `crew-setup` skill with **no new tools** (only `ask_question` and deep links), 8–10 newcomers against 8–10 who use the chapters | Median > 15 min, or completion no better than the chapters |
| 4 | The Egg fits its budget | A stopwatch with 5 people new to Kiro, splitting machine time from human time | Harness sign-in alone > 2 min at p50: change the flow or the default |

Test 3 is also the cheapest *rival solution*. If a skill that uses only deep
links does as well as the chapters, most of Phase 2's value is in the
conversation, not in the new tools, and Phase 2 shrinks.

## 10. North star: the crew that lives on its own machine and builds itself

The long-term shape is one conversation that takes a user from install to a
crew that:

- lives permanently somewhere they own;
- moves itself there without leaving the chat;
- builds the connectors it is missing;
- follows them onto every device.

Each step below is its own RFC and is entered only when its gate is met. What
revision 1 worked out for each step is kept here, so the follow-up RFCs start
from it.

### 10.1 A nest, offered when it hurts

- **Gate:** a fake-door card ("Keep me running when your laptop sleeps") is
  clicked by more than 10% of activated users within 4 weeks.
- **First kind: BYO over SSH** (a Mac mini, home server or VPS). The gateway
  generates a key per nest and shows the public half in a key card, with copy
  and "email it to me". The host key is pinned only after the user confirms its
  fingerprint. Password auth is never used. Bootstrap runs
  `start.sh --nest --pair <code>` headless.
- **Moving there is a handoff, not live migration:** a portability export
  (`src/kiro_crew/portability.py`) plus the existing session transfer
  (`src/kiro_crew/dashboard/session_transfer.py`), and a card that says "here's
  what I brought; here's what to reconnect".
- **Invariants that move with it:** the model never sees private key material
  or AWS credentials (SC4); a cron runs on exactly one crew at a time (SC5);
  bootstrap never uses password auth and pins the host key only after
  confirmation (SC7).
- **Transport:** the SSH tunnel first, then the outbound transport (#13031),
  which removes inbound ports and router port-forwarding altogether.
- **A tailnet as the reach-back path.** Muse's hosted computer joins the user's
  Tailscale network as a client only: `tailscale up` prints a link, the user
  approves the device, and each connection to one of the user's machines needs
  its own approval. A home in the cloud could reach the user's laptop or NAS the
  same way, with no inbound port on either side. That is a new egress route, so
  it waits for the egress authority (§10.6).

### 10.2 EC2, for developers

- **Gate:** nest adoption above 15% of activated crews.
- It is shown only behind `user_technical_level = codes`.
- It reuses the launch engine (`run_launch` in `src/kiro_crew/cloud/launch_job.py`)
  and the CloudFormation template, which already provides SSM access, an
  encrypted EBS root, an instance role, IMDSv2 and no inbound port 22 unless
  `AllowSshCidr` is set.
- A cost card is shown before anything happens. Today's cheapest tier is
  `t4g.xlarge` at about $98/month and the recommended one about $238/month
  (`src/kiro_crew/cloud/sizes.py`), so this RFC measures whether a smaller tier
  fits.
- A no-credentials console quick-create path depends on #13031.

### 10.3 Agent-built connectors

- **Gate:** the egress-authority RFC is accepted, and logged misses — requests
  that neither registry can serve — exceed 5 per 100 active crews per month.
- The design waiting for that RFC:
  - the connector ships as an App Kit app or a local MCP server, with a filled
    [connector-capability-manifest](../system-specs/modules/connector-capability-manifest.md);
  - its code uses `secret://` references, and the values come from a capture
    card;
  - it must pass a smoke test before it is offered;
  - it goes through admission (`src/kiro_crew/apps/admission.py`) with a
    preview card listing its hosts, write operations and secrets;
  - it runs in the app sandbox;
  - it can be published to the registry on opt-in.
- Until egress is enforced, the manifest's host list is a declaration, not a
  fence. That is why this step waits.
- **What Muse does, and what to take from it** (Appendix F):
  - Collecting a credential is one tool with a fixed order: it checks the
    provider first, then mints a hosted link where the user types an API key or
    signs in with the provider's own OAuth. It declines, by name, providers that
    need a password login, session cookies, request signing or more than one
    secret. The credential card (§6.3) is that step; the refusal list belongs
    in it.
  - A scaffolder then writes the connector skill's Tooling and Auth sections
    from the stored connection (which helper attaches the credential, which
    hosts are allowed, how to replace a credential that stops working); the
    agent writes only the command-line tools and leaves those sections as
    generated. Kiro Crew's equivalent generates the manifest's host list and
    the `secret://` binding, so the agent never writes auth code.
  - Permissions are declared per method: each action group (read, write) has
    a default of Allow, Ask or Deny, a method can override it, and each method
    names the OAuth scopes it accepts. A standing grant can be scoped to one
    scheduled job or one app ("this job may send to this recipient"). The
    connector-capability-manifest already has one row per operation; the
    default and override columns are what it lacks.
  - A connector can do exactly what its skill documents. A command the skill
    does not list does not exist, and the agent says so rather than improvising
    against the API.

### 10.4 Every device

- **Gate:** activation targets are met.
- The signed desktop app is personalised at runtime (theme, name, avatar); a
  personal unsigned build triggers OS warnings.
- The PWA gets a per-install manifest with the agent's name and icon, paired by
  QR code (`src/kiro_crew/dashboard/handlers/auth_mobile.py`).
- Native wrappers are later apps, not core.
- Client preferences that name endpoints must be validated. A user-writable
  endpoint preference was how Muse's macOS token theft worked.

### 10.5 Live move-in and consolidation

- Live migration of a running crew is entered only if the concierge moves of
  §10.1 show that the handoff loses something users care about.
- Consolidation is the last step: `kirocrew setup` hands off to the first run,
  and cloud UserData and `cloud-install.sh` use `start.sh --nest`, so every
  machine installs through one code path. It comes after everything above has
  shipped.

### 10.7 Managed hosting: a home with no AWS account

The parallel home (§6.8) still asks for an AWS account, and for many people that
signup is the moment they give up. The next step removes it: a hosted provider
behind the same `RemoteProvisioner` seam, drawing from capacity prepared ahead
of time so a home is ready in seconds rather than a CloudFormation deployment's
minutes, billed as credits or a subscription rather than a per-resource bill,
and released when it goes unused. It is also what makes a phone app a possible
Egg — a phone cannot host a crew, so installing on a phone means "create my
home somewhere". This needs a backend service the project does not have yet,
so it is its own RFC; the chat, the cards and the move-in do not change when it
arrives.

### 10.6 Needed before 10.3: an egress authority

The egress authority would provide:

- per-host and per-protocol network approvals, in plain-language rows ("Outbound
  SSH, port 22"; "Database connections"; …);
- SSRF checks on the resolved address;
- credential insertion at egress;
- taint-based auto-allow.

Scoped grants on approvals (one-time, session, task, time-bounded, perpetual,
with one-step promotion) belong beside it.

## 11. Backward compatibility

- `cli.sh`, `install.sh`, `kirocrew setup`, the cloud wizard and the chapters
  keep working unchanged.
- An already-onboarded install never gets a first-run session, and `/onboarding`
  still opens the chapters.
- Every new config key defaults to today's behaviour.
- The heartbeat gains one value in an existing closed set, and no new field.

## 12. Alternatives considered

- **Improve the chapters.** Tested rather than argued: Phase 0 test 3 runs the
  chapters against a conversational setup built from deep links only.
- **Make `cli.sh` start the gateway and open the browser, and do nothing else.**
  This fixes problem 1 alone, and Phase 1 ships it anyway. It is not rejected;
  it is the Egg. It does not fix problems 2–5.
- **The full revision-1 scope in one RFC.** Rejected in favour of §10: most of
  the risk and effort sat in steps the evidence does not yet support.
- **Nest before connectors,** which saves an OAuth re-consent later. Rejected:
  it puts the least-trusted, most complex decision ahead of any value.
- **A thorough persona questionnaire.** Rejected: questions come before value.
  Defaults plus learning from corrections serve the user better.
- **Letting the model write config through its file tools.** Rejected by SC1 and
  SC3.

## 13. Open questions

- **Q1.** Is start.sh generated from `cli.sh` at publish time, or is it
  `cli.sh --first-run` behind a thin wrapper? Either way there must be one trust
  root. *Prototype answer:* neither — start.sh downloads the live, unmodified
  `cli.sh` from the same origin, runs it (it verifies the signed manifest), then
  execs `kirocrew start`. `cli.sh` stays byte-identical, which is Phase 1's exit
  criterion.
- **Q2.** May the installer install a pinned, verified kiro-cli? PR #13888 lists
  "install or authenticate a harness from inside Kiro Crew" as a non-goal. The
  desktop bundle already ships a pinned kiro-cli. This needs a maintainer
  decision and a check of kiro-cli's distribution terms. *Prototype answer:* it
  does not — `kirocrew start` prints the official install guidance and exits 3
  when the harness is missing, and offers kiro-cli's own sign-in command (device
  flow on a headless host) when it is installed but signed out.
- **Q3.** Should SOUL.md be read-only to in-sandbox code, to close the
  persistent-injection path? That would be a new seal, and seals are the
  operator's call.
- **Q4.** Where do the primary agent's SOUL.md and USER.md live, and how do they
  compose with per-crewmate SOUL.md files and a theme's persona.md?
- **Q5.** Is "the chapters no longer auto-open for first-run installs" covered by
  rfc-crewmates-launch, or does it need its own recorded decision?
- **Q6.** Which connector family goes first, GitHub or Linear/Atlassian? Phase 0
  decides.
- **Q8.** The smallest instance a home runs on well, and its honest monthly
  cost. Today's cheapest tier is `t4g.xlarge` at about $98/month
  (`src/kiro_crew/cloud/sizes.py`); a home that only runs the gateway and the
  harness is likely far smaller. Measure before offering it. *Prototype
  measurement:* an idle gateway with one open chat uses about 0.6 GB (the gateway
  about 0.2 GB, one kiro-cli chat process about 0.25 GB), and each further open
  chat adds about 0.25 GB. What rules out a 2 GB instance today is not the
  runtime but the install: the home builds the dashboard on the box, and that
  build is given a 6 GB heap. A home installed from the prebuilt wheel `cli.sh`
  ships would not build anything, so the smallest tier depends on the home
  installing the release artifact rather than building from source.
- **Q10.** Honest free-plan wording. A new account's Free plan (up to $200 in
  credits, no charge unless upgraded, closes after six months) covers only
  small instances such as `t4g.small` (~$18/month); today's smallest home tier
  is not eligible (~$101/month on the paid plan). "Free for six months" is true
  only if Q8's measurement finds a 2 GB home workable.
- **Q9.** AWS account creation cannot be automated. How much of the signup can
  the Egg smooth (a direct link, a checklist, resuming after signup), and at
  what point does the managed provider (§10.7) replace it?
- **Q7.** Vocabulary. Revision 1 used hatch, egg and nest. "Hatch" is Meta's
  internal codename for Muse and also OpenClaw's onboarding verb ("hatch your
  bot"). This revision proposes plain UI labels ("Setup", "Home") and keeps
  "Egg" only as a design term here. Is that settled?
- **Q11.** Should the dashboard refuse to delete the main chat, as Muse does, or
  only warn? Refusing makes "main" a small behaviour and not only presentation
  (SC6 still holds: it gates a delete, never what a turn may do).
- **Q13.** How does the main chat get the session tools: a `kirocrew-main` spec
  that graduation switches the chat to (the default agent's tools plus the
  conductor's `kirocrew-dashboard` grants), or the default agent mounting
  `kirocrew-dashboard` for every chat? The first keeps every other chat as it
  is; the second changes what every session's context carries. *Prototype
  answer:* the first, without a switch: the first-run chat is created on
  `kirocrew-main`, because changing a chat's agent later resets its session.
  `kirocrew-main` mirrors the on-disk default spec (so a server a connect card
  adds is there too) and adds `@kirocrew-dashboard`, with `session_create` and
  `session_read_message` granted and `session_send` / `session_stop` left to the
  approval gate. The same spawn-path freshness check as `kirocrew-worker` keeps
  it in step with the default, and the code that treated every agent other than
  `kirocrew` as custom now asks `is_primary_agent`, so the main chat keeps
  SOUL.md, USER.md and the skills.
- **Q14.** On Windows, `start.ps1` runs the desktop app's bundled `kirocrew`
  outside the app, so it does not see the app's bundled kiro-cli and reports the
  harness missing on a machine that has only that copy. Should it point at the
  bundled copy, as the app's own launcher does, or open the desktop app instead
  of the terminal flow? Tied to Q2. *Prototype answer:* point at it. `start.ps1`
  hands the install's bundled kiro-cli to `kirocrew start` exactly as the app's
  launcher hands it to the gateway, only after it answers `--version`. Nothing
  new is installed, so Q2 stays open for machines without the app.
- **Q15.** Copying this computer's Kiro sign-in to the home (§6.8 rule 5):
  does a copied refresh token survive two machines refreshing it, for Builder
  ID, social and Identity Center sign-ins? And do Kiro's terms allow one
  person's sign-in on two of their own machines at once? *Answered for v1
  (2026-09-28, the user):* no copy; the home keeps its own sign-in, made one
  click (the page opens with the code prefilled). The copy stays a later spike,
  because a rotating refresh token shared by two machines risks signing the
  local kiro-cli out.
- **Q12.** The first-week notes (§5.8): one a day for seven days, or fewer?
  *Prototype answer:* one a day, at most six (one per unset thing), and they
  cost no quota, because each is a fixed notice rather than a model turn.

## Appendix A: Muse, condensed, with the security mapping

**The product.** Launched in the US on 2026-09-08, on Muse Spark 1.3.

- **Pricing.** A free tier with weekly limits, plus paid tiers at $20 and $100.
- **Traction.** About 560K daily users after 11 days.
- **Onboarding.** Meta sign-in, a plain-language intro and a training
  disclosure (training is on by default), then naming the agent, then
  connectors one at a time, then an Ideas tab. The alpha's three screens were
  "Hatch is better with connectors", "Put your agent to work" and "Customize
  your agent".
- **Worst reported setup bug.** Google OAuth on mobile bounced users to the
  website instead of the app.
- **Custom connectors.** They run inside the agent's cell without privilege
  separation, and one reviewer found their credentials did not persist.
- **Incidents.**
  - A macOS token theft through a user-writable dictation-endpoint preference.
  - Users exported the VM's whole filesystem ("intended behavior").
  - Reviewers found the interest profiling creepy.
- **Comparables.**
  - OpenClaw: `curl … | bash`, then an automatic
    `openclaw onboard --install-daemon`, with SOUL.md, USER.md and IDENTITY.md
    (which Meta admits Muse copied).
  - Hermes: a one-line install, then `hermes setup`, with OpenClaw detection
    and migration.

| Muse component | What it does | Kiro Crew today |
|---|---|---|
| Ingress (TLS + Noise) | Authenticated client-to-VM channel | Loopback bind, token auth, tunnels, SSH/SSM forwards |
| Runtime cell (nspawn; guest root ≠ host root) | Confines the harness and tools | `src/kiro_crew/sandbox.py`: user namespace on Linux, Seatbelt on macOS |
| execd (seccomp) | Syscall filtering | Namespace isolation only |
| Runtime network (veth + eBPF proxy) | Egress chokepoint | None; §10.6 |
| LUKS | Encryption at rest | The EC2 template encrypts EBS; local disks rely on the OS |
| hatch-safety classifiers | Independent review | PreToolUse gate (`src/kiro_crew/hooks.py`), denied commands, SEL audit |
| Inference proxy | Model transport | The ACP harness |
| hatch-authd | Credential storage and surrogate tokens | The vault with `secret://`; kiro-cli-owned OAuth grants; hidden credential leaves |
| Sentinel | Sole permission authority; allow/deny/ask; scoped grants | Governance `POLICY ∩ PROFILE` at Kiro Crew's own gate (`src/kiro_crew/platform/governance.py`); approval cards; read-only keystone files; **cards (§6.3)** |
| privsep broker (`SO_PEERCRED`) | Credential-capable code outside the cell | The MCP gateway process; vault resolved at server spawn |
| Approvals UI, direct to the authority | Approvals bypass the chat | Approval cards; **card clicks go to a gateway endpoint, never through model text** |
| Credential Capture UI | Secrets never in chat | **New:** the credential capture card and pasted-secret redaction |
| Telemetry proxy | A constrained telemetry path | The daily heartbeat (five fields), with opt-out |

**Primary sources.**

- Meta, "How We Built Safety Into Muse":
  <https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse>
- Meta Newsroom:
  <https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/>
- "How We Designed Muse": <https://introducing.muse.ai/>
- TechCrunch on Muse and OpenClaw:
  <https://techcrunch.com/2026/09/22/meta-admits-muses-likeness-to-openclaw-isnt-a-coincidence/>
- OpenClaw: <https://github.com/openclaw/openclaw>
- Hermes Agent: <https://github.com/NousResearch/hermes-agent>

## Appendix B: the 2026-09-27 Muse session

| # | What happened | Lesson |
|---|---|---|
| 1 | Asked for a name before anything else | The name comes early (§5.2) |
| 2 | Connected Gmail, then listed three things it could now do | A capability reveal after every connection (§5.3) |
| 3 | Offered its SSH public key and to email it | The north-star key card (§10.1) |
| 4 | "Be smart about it": made a 30-minute watch with no more questions | Defaults over questions (§5.2) |
| 5 | Blocked on a permission that exists only in the web app; "I do not see it" | Every permission is a card in the chat (§6.3) |
| 6 | Three turns of router port-forward debugging | A nest should dial out (#13031); BYO SSH is a power-user path (§10.1) |
| 7 | Imported six crons: adapted delivery, merged two, dropped one, asked the language | Adapt imports; don't copy them (§5.3) |
| 8 | Each connected Google service came with concrete next uses | The same for every connector and channel |

## Appendix C: the product critique behind revision 2

The critique measured revision 1 against:

- SVPG's four risks;
- Torres's continuous discovery;
- Jobs-to-be-Done;
- the Mom Test;
- riskiest-assumption testing and pretotyping;
- Working Backwards;
- activation research;
- Hooked and its ethical critiques;
- Shape Up;
- Kano and RICE;
- published agent-UX guidance from Anthropic, OpenAI and Google PAIR.

Its verdict was **"go, with changes; split and cut hard."**

**Findings this revision acted on:**

- **Value risk.** A general personal agent does not retain on its own. ChatGPT
  agent reportedly lost about 75% of its users over unclear purpose, and AI
  subscription apps retain 21.1% of annual subscribers against 30.7% for non-AI
  apps. → §2.3 targets developers and switchers, with dev-signal jobs.
- **Late value.** The first value arrived the next morning. → Preview-now jobs
  (§5.4) and G2.
- **Investment before reward.** The persona questionnaire and the nest came
  before any value. → Light soul (§6.5), with the nest offered when it hurts
  (§5.5, §10.1).
- **Approval fatigue.** Users approve about 93% of prompts. → The card budget
  and distinct styling for high-stakes cards (§6.3).
- **Prompt-injection surface.** Setup proposals could come from any turn. → SC8.
- **User behaviour.** Users paste secrets into chat anyway. → §6.6.
- **Viability.** The free-tier quota, and EC2 at $98–$238 a month. → §6.7 and
  §10.2.
- **No appetites, kill criteria or cheap tests.** → §8 and §9.
- **"Hooked".** Retention should come from value delivered asynchronously —
  messages carrying new information, variation that comes from the world, and
  investment that stays portable — not from engagement mechanics. v1 rules out
  streaks, "I missed you" pings with nothing new, guilt-laden persona copy and
  proactive messages in quiet hours.
- **Naming.** "Hatch" overlaps with both Meta and OpenClaw. → Q7.

**Sources.**

- SVPG, four big risks: <https://www.svpg.com/four-big-risks/>
- Torres, assumption testing: <https://www.producttalk.org/2023/10/assumption-testing/>
- The four forces: <https://jobstobedone.org/the-four-forces/>
- Pretotyping: <https://www.pretotyping.org/>
- Activation benchmarks: <https://www.lennysnewsletter.com/p/what-is-a-good-activation-rate>
- NN/g, buttons in generated UI: <https://www.nngroup.com/articles/genui-buttons-and-checkboxes/>
- Hooked: <https://www.nirandfar.com/how-to-manufacture-desire/>
- Calm technology: <https://calmtech.com/>
- Shape Up: <https://basecamp.com/shapeup/1.2-chapter-03>
- Claude Code sandboxing (the 93% approval figure): <https://www.anthropic.com/engineering/claude-code-sandboxing>
- Google PAIR on trust: <https://pair.withgoogle.com/chapter/explainability-trust/>
- The lethal trifecta: <https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/>
- AI app retention: <https://techcrunch.com/2026/03/10/ai-powered-apps-struggle-with-long-term-retention-new-report-shows/>
- ChatGPT agent usage: <https://the-decoder.com/chatgpt-agent-reportedly-lost-75-of-its-users-because-nobody-knew-what-it-was-actually-for/>
- Kiro billing: <https://kiro.dev/docs/billing/>

## Appendix D: the team review behind revision 3

Revision 2 was discussed with teammates on 2026-09-27. The points that changed
the plan:

- **Onboarding must not get longer.** Users must be able to use the crew right
  after installing. Anything slow — building a remote home, for one — runs as a
  background job while the user does the main thing, like the embedding models
  that download without interrupting. → G8, §6.8 rule 1.
- **The harness is already bundled** in the latest desktop build, so the
  desktop Egg has no harness step.
- **Many users have no AWS account, and a paid, open-ended AWS bill is a hard
  sell** next to a free hosted assistant; a ten-minute signup detour is enough
  for some to quit. → the cost card, Q8 and Q9, and §10.7.
- **The lasting answer is managed hosting**: a hosted backend with capacity
  prepared in advance, billed as credits or a subscription, which also makes a
  phone app a possible install surface. Backend work toward long-lived hosted
  agents has started. → §10.7, behind the same provisioner seam.
- **Say it plainly.** "AWS" and "EC2" read as scary to non-technical users;
  concepts need friendly names. → plain labels in the product, while the cost
  card still names who bills the user (§6.8).
- **Mock it first.** The experience can be shown before it is fully built. →
  the simulated launch engine (§6.8) and Phase 0's Wizard-of-Oz test.
- **The agent should set things up; the tools already exist.** Cloud launch is
  already a command; what was missing is exposing it to the agent and the chat.
  → the setup tools and cards (§6.3, §6.4).

## Appendix E: can AWS sign-in feel like OAuth inside the chat?

Research of 2026-09-27 against AWS documentation and announcements:

- **No third-party "Sign in with AWS" consent screen is open to a self-hosted
  app.** IAM temporary delegation (the "Allow access" page a hosted product like
  Vercel uses) is limited to approved partners; cross-account roles with an
  external ID and OIDC trust both need a vendor AWS principal or a public
  issuer, which a self-hosted install does not have. AWS Sign-in's OAuth 2.1
  endpoints serve the AWS MCP Server only.
- **`aws login` is the practical OAuth.** It opens the browser, uses the
  authorization-code flow with PKCE, and leaves short-lived credentials in the
  CLI's own cache; a headless host uses `aws login --remote`. Kiro Crew runs it
  from the home card as a child of the gateway, with no terminal, and never reads
  its output or its cache.
- **Account creation**: no API creates a standalone account. The September 2026
  sign-up (Google, GitHub, Apple or Amazon login; usually no card; spend limits;
  a region fixed by country) makes it a few minutes rather than ten; the
  classic sign-up still asks for a card and a phone check.
- **Launch Stack links** would remove local credentials entirely, but the new
  instance then needs something to phone home to; with no project backend that
  belongs to the managed-hosting follow-up (§10.7), as do vendor-owned accounts
  created through AWS Organizations.
- **The 12-hour horizon**: the local dashboard reaches the home over SSM, which
  needs a valid AWS sign-in; after it lapses the owner signs in again (channels
  such as Slack connect outbound and are unaffected).

## Appendix F: what Muse's agent-side snapshot shows

On 2026-09-28 a publicly posted, partial snapshot of a Muse agent's computer
was read: the product documents written for the agent, 68 skills, 40 connector
permission manifests, 12 evaluation files and the scripts that boot the
per-user runtime. Most programs in it are binaries and its provenance is
unverified, so it is treated as one more observation, like Appendix B. Nothing
from it is copied into this repository; only the lessons are.

| # | What Muse does | What this RFC takes |
|---|---|---|
| 1 | One **main chat**, which can never be deleted; side chats by topic, which can be archived; every channel conversation is its own side chat and is never mirrored into the main one; the Feed learns taste from the main chat only | §6.9, Q11 |
| 2 | Moving work to a side chat: offer once, create only after a yes, never ask again for that task, never from a side chat, send a self-contained brief, do not poll | §6.9, `crew-setup` |
| 3 | "Edit" on a scheduled task and "Add subgoal" on a goal draft a chat message for the user to send, instead of opening a form | §6.9 (MC.6) |
| 4 | The feature tour is a scheduled job over the first days; a new reader's Feed opens with fixed "Getting started" posts written in the agent's voice that draw on none of the user's data | §5.8 |
| 5 | Context from another AI: a copyable prompt for the old assistant, the user reviews and trims its answer, pastes it back, and the answer is treated as material, not instructions | §5.3 |
| 6 | Persona files the user can open and edit (identity, soul, user, memory), plus a proactive-preferences file: what to bring up, what never to, when, and how | §6.5; proactive preferences fit USER.md (Q4) |
| 7 | A status screen with Activity, Approvals, Upcoming and Identity; approvals reach the phone as a push with Allow and Deny | The overview's counterpart for people (§6.9); §10.4 |
| 8 | Scheduling is described honestly ("I'll check every 30 minutes", never "the moment it happens"); run history, not the schedule, answers "did it run"; a job can be marked blocked on a dependency, with a next probe time | Cron card copy (§5.4); a blocked-on-connection state for first-run jobs |
| 9 | A connector does exactly what its skill documents; connection state is checked each turn, never recalled; a permanently rejected grant is reported with its reconnect link, and a write is never replayed on its own | §10.3; connect-card errors (§6.3) |
| 10 | Credential collection is one ordered tool with a named refusal list; a scaffolder writes a new connector skill's auth sections; permissions are per method with group defaults, overrides and per-job grants | §10.3 |
| 11 | Background upkeep: memory consolidation, a page per person, ideas ranked for fit and novelty, nightly reflection, and a daily skill review that turns repeated work into skills and retires the ones that do not help | The north star's "builds itself" (§10); `crystallize` in v1 (§5.6) |
| 12 | Evaluation scenarios next to each skill: persona, objective, mocked world, a rubric line | §8 persona evals |
| 13 | Each user has a dedicated computer; skills are revealed per release channel and fail closed, and the scripts say outright that hiding a tool is not an authorization boundary | The shape §10.7 aims at; nothing for v1 |
| 14 | The hosted computer joins the user's Tailscale network as a client only, each join approved by the user from a link | §10.1 |
| 15 | "Queued" and "delivered" are kept apart: a successful run alone does not prove the user got the result | The overview and `setup_status` report what happened, not what was attempted |

What the snapshot does not show: an install (Muse is hosted, so a person signs
up in an app and the computer already exists), the script of a first
conversation, or any outcome data. It supports §6.9 and §5.8 as patterns, not
as evidence that they retain users.
