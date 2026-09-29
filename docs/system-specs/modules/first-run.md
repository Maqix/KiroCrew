# First run and setup cards

The one-chat first run gives a fresh install a single pinned chat in which the
agent sets Kiro Crew up with the user. Every change the agent wants is a
**setup card**: a server-side pending action rendered inline in the chat, which
commits only when the owner clicks it. The design and its rationale are in
[rfc-one-chat-first-run.md](../../request-for-change/rfc-one-chat-first-run.md);
this spec is the contract the code keeps.

## Pieces

| Piece | Where | Role |
|---|---|---|
| First-run state | `src/kiro_crew/first_run.py` | `data_home()/setup/first-run.json`: the first-run slot key, the stages done, the main chat, first-week tip state, held hand-off notices, and a home answer `kirocrew start --home` recorded for a script. Every change to one key goes through `update_state`, which serializes read-change-write in the gateway process so two writers cannot drop each other's keys. Presentation only. |
| Card store | `src/kiro_crew/setup_cards.py` | The `SetupCard` record, the durable store `data_home()/setup/cards.json`, payload hashing, per-kind argument validation, the persona files. |
| Flow | `src/kiro_crew/dashboard/setup_flow.py` | `propose` (the directive applier), `decide` (the owner's click), the per-kind committers, `ensure_first_run_session`, `start_first_run_turn`. |
| HTTP | `src/kiro_crew/dashboard/handlers/setup_cards.py` | `GET /api/setup/first-run`, `POST /api/setup/first-run/retry`, `GET /api/setup/cards?slot=`, `GET /api/setup/cards/{id}`, `GET /api/setup/cards/{id}/approvals`, `POST /api/setup/cards/{id}/decide`. All owner-only. |
| Guardrails | `src/kiro_crew/dashboard/setup_guardrails.py` | The stall watchdog, the kickoff notice and the quota pause (see [Guardrails](#guardrails)). |
| MCP tools | `src/kiro_crew/mcp_tools/setup.py` | `setup_card` (a session directive) and `setup_status` (read-only). |
| Channel pairing | `src/kiro_crew/dashboard/setup_channel.py` | The `channel` card's commit (the bot token) and its `/pair` code, which lives in process memory only. |
| Job preview | `src/kiro_crew/dashboard/setup_preview.py` | The `cron` card's preview run: the approvals it waits on, shown on the card, and a verdict that counts how they ended (see [Job previews](#job-previews)). |
| AWS sign-in | `src/kiro_crew/dashboard/setup_aws_signin.py` | The `home` card's Sign in to AWS: runs the AWS CLI's own `aws login` from the card and watches for it to land (see [Signing in to AWS](#signing-in-to-aws)). |
| Home sign-in | `src/kiro_crew/dashboard/home_signin.py` | While the home's build waits on its own Kiro sign-in: opens the sign-in page once in the owner's browser when they are on this machine, and posts the one `home_signin` notice (see [The home's Kiro sign-in](#the-homes-kiro-sign-in)). |
| Moving in | `src/kiro_crew/dashboard/setup_move_in.py` | The `home` card's Move in for a live home: reach it over the Instances tunnel, export, import there, send the chat (see [Moving in](#moving-in)). |
| Pasted secrets | `src/kiro_crew/dashboard/secret_capture.py` | Moves credentials a user pasted into chat into the vault before the message is stored or sent. |
| Skill | `src/kiro_crew/builtin_skills/crew-setup/SKILL.md` | How the agent runs the first run and any later setup request. |
| Persona context | `src/kiro_crew/context.py` `_build_persona_files_section` | `[AGENT PERSONA]` / `[USER NOTES]` from `data_home()/persona/SOUL.md` and `USER.md`. |

## Lifecycle of a card

1. The agent calls `setup_card(kind=…)`. The tool validates the arguments and
   returns a `setup_card` session directive; it changes nothing.
2. The session's consumer applies it through `apply_session_directive`
   (`dashboard/session_directive_apply.py`). `setup_card` is a dashboard-only
   directive, so a slot-less or tabless caller is refused there.
3. `setup_flow.propose` refuses a turn no person started, a governance denial, a
   kind the model may not propose (`privacy`), a proposal past the card budget,
   and a duplicate of a pending card. Otherwise it builds the payload the owner
   will see (an import preview, a provider lookup, the service platform, the
   current persona file), stores the card, appends an `inject` row whose meta is
   `{"setupCard": {"id", "kind"}}` (no `injectKind`, so it opens no turn), and
   sends the owner-only `setup_card_update` event.
4. The browser renders the card from `GET /api/setup/cards/{id}`, never from the
   row's text.
5. The owner's click posts `{decision, hash, input}` to `/decide`.
   `setup_cards.claim_pending` moves a `pending` card whose stored hash equals
   the posted hash to `working`, atomically, under the store lock; anything else
   is refused (`card_not_pending`, `card_hash_mismatch`). Governance is checked
   again. The committer runs.
6. A recoverable failure (an empty credential, a service not installed yet)
   returns the card to `pending` with `error` set. A terminal status
   (`committed`, `declined`, `failed`, `expired`) is reported to the agent as a
   `[Setup card result]` envelope turn in the card's chat.

Statuses: `pending`, `working`, `waiting` (outside action such as an OAuth
consent page), `committed`, `declined`, `failed`, `expired`. A stored record
whose payload no longer hashes to its `payload_hash` is read back `expired` with
`error.code == "card_tampered"` and can never be claimed.

## Kinds

| Kind | Proposed by | Payload shown | Commit does |
|---|---|---|---|
| `privacy` | the gateway only | the privacy disclosure (frontend strings) | sets `dashboard.privacy_acked`; `telemetry.beacon_enabled = false` when the owner turned telemetry off; starts the first model turn |
| `profile` | agent | `fields`: bot_name, language, timezone, technical_level, role | writes those config keys through `update_config_locked` under `run_config_write`, then a hot apply |
| `soul` | agent | `file` (`SOUL`/`USER`), `content` (≤ `SOUL_MAX_CHARS`), `previous` | writes `data_home()/persona/<file>.md` |
| `import` | agent | detected sources and categories with counts | `onboarding_import.run_import_apply` — the same lock order and re-scan as the Import chapter; imported jobs arrive disabled, and the result names them (name, schedule, a prompt excerpt) because the chat's session-scoped job tools do not list jobs the import created |
| `connect` | agent | a curated provider (`registry.json`), whether it needs an operator OAuth client | writes the remote MCP entry (`mcp_custom.ensure_remote_server`), starts the mint (`connections.start_provider_mint`), goes `waiting` with the consent URL, and a watcher follows `pending_mint_for` to `committed`/`failed`/`expired` |
| `credential` | agent | name, purpose, hosts, whether the name exists | stores the typed value in the vault; the outcome is only `secret://NAME` |
| `channel` | agent | the channel (Telegram) | stores the bot token typed into the card as `TELEGRAM_BOT_TOKEN` in `.env` through the Settings save's own helper (`messaging.commit_telegram_writes`), turns `telegram.enabled` on and reconnects the channel, then goes `waiting` with a one-time 4-digit pairing code. A `/pair <code>` DM to the bot adds that sender's id to `telegram.allowed_user_ids` and commits with `{channel, paired, username}`; five wrong codes fail the card, and ten unpaired minutes expire it. See [Channel pairing](#channel-pairing) |
| `cron` | agent | name, prompt summary, schedule in words, timezone (the full prompt is private) | `preview`: creates the job disabled and silent, runs it once, shows the output (status `success`, `failure` or `timeout`, mapped from the cron run's own status, and `failure` with `reason: approval_not_given` when an approval the run asked for was rejected or went unanswered), returns to `pending`. While it runs, the card shows the run's pending approvals with Allow once and Reject; after a run that asked, `approvals` counts them and the card says the job will ask on every run, in Notifications. See [Job previews](#job-previews). `commit` (Keep it): makes it non-silent and enables it. `decline`: removes the preview job |
| `service` | agent | platform, the command, whether a terminal is needed, installed | macOS: installs the launchd agent. Linux: verifies the unit exists (the owner runs `kirocrew stop && kirocrew service install`, which needs sudo) |
| `home` | agent, or the gateway when a script passed `kirocrew start --home cloud` | provider, region (the account's own, once AWS answers), AWS profile, the size options with what each costs and whether it needs AWS's paid plan (see [The home's size](#the-homes-size)), the account's plan when signed in, estimated monthly cost, who bills it, whether AWS is signed in, whether the run is simulated | two decisions on one card, after **Sign in to AWS** when AWS was not signed in: the `aws_signin` decision runs `aws login` from the card, goes `waiting`, and returns to `pending` with `aws_signed_in: true` in the outcome once AWS answers (see [Signing in to AWS](#signing-in-to-aws)). When no region answers, **Use this region** (the `region` decision, `input.region`) re-issues the card for the owner's pick (see [Asking for the region](#asking-for-the-region)). **Build my home** (`input.size`, one of the offered sizes): records whether the owner's browser is on this machine, checks the size against the plan and the vCPU quota, starts the launch job (`handlers_cloud.start_launch_job`), goes `waiting`, and a watcher mirrors the build's steps onto the card until it is done (`pending`, `ready`) or fails; while the build waits on the home's own Kiro sign-in, see [The home's Kiro sign-in](#the-homes-kiro-sign-in). A build that finished without that sign-in is not `ready`: the card is `pending` in phase `signin` with `needs_signin`, and its commit is **Sign the home in to Kiro** (`handlers_cloud.restart_signin`). **Move in**: a simulated home walks four steps and moves nothing; a live one is handed the crew and this chat, see [Moving in](#moving-in). Commits with `moved: true` |

## Invariants

| Id | Rule | Pinned by |
|---|---|---|
| SC1 | No card commits without an owner decision carrying the payload hash the owner was shown. | `test_setup_flow.py::TestDecide::test_s1_a_wrong_hash_commits_nothing`, `test_setup_cards.py::TestStore` |
| SC2 | A credential typed into a card never appears in the card, the store, the transcript, an event or a log record. | `test_setup_flow.py::TestDecide::test_s2_a_credential_reaches_the_vault_and_nowhere_else`, `test_setup_channel.py::TestCommit::test_s2_the_bot_token_reaches_the_credential_file_and_nowhere_else` |
| SC3 | No committer writes a governance keystone file or the sandbox/approval mode. | `test_setup_flow.py::TestDecide::test_s3_no_committer_writes_a_keystone_file` |
| SC4 | Kiro Crew never reads or stores an AWS credential: the AWS CLI resolves and caches its own. The card's `aws login` child has every standard stream closed, and a card keeps at most the account's last four digits. | `test_setup_aws_signin.py::TestNoCredentials` |
| SC5 | A schedule runs on exactly one crew during a move-in: the local copies are off before the archive reaches the home, back on when the home does not confirm it, and stay off once it has. | `test_setup_move_in.py::TestHappyPath::test_sc5_the_moving_job_is_off_here_before_the_archive_lands`, `TestCarryFailure`, `TestRetry` |
| SC6 | The first-run state file admits nothing. | `test_setup_flow.py::TestPropose::test_s6_the_first_run_state_file_admits_nothing` |
| SC8 | A card is raised only in a turn a person started: a typed message, or a turn that exists because the owner clicked a card (the first-run kickoff and every `[Setup card result]` turn carry user provenance for that reason). | `test_setup_flow.py::TestPropose::test_s8_a_turn_no_person_started_shows_nothing` |

## Channel pairing

RFC §5.3 connects one channel "through a credential card for the bot token,
plus a `/pair 4821` message that allowlists the user's own ID without asking
them to look it up". Telegram is the one channel wired
(`setup_cards.CHANNELS`).

- **The token.** The card's secret field posts `input.token`. The commit
  shape-checks it (`messaging.clean_telegram_token`), verifies it with `getMe`
  (a rejection returns the card to `pending`; offline stores it unverified),
  and stores it with `messaging.commit_telegram_writes`, the Settings save's
  Phase 2: `config.json` first (`telegram.enabled = true`, the legacy
  `telegram.bot_token` purged), then `.env`. The Telegram channel reads the
  literal `.env` value and does not resolve `secret://` references, so the
  token is not put in the vault; `.env` is hidden from the agent in every
  sandbox mode. Enabling the channel or purging the legacy token is a boot-key
  change the config watcher answers by reconnecting the channel; a token
  swapped under an already enabled channel asks the gateway through
  `DashboardState.restart_channel` instead.
- **The code.** Four digits from `secrets`, live for ten minutes, one-time,
  one per channel (a newer card's code expires the older card with
  `pair_superseded`). It is held in `setup_channel`'s process memory and
  checked against memory only: the card store is readable and writable from
  the agent's sandbox, and the model reads cards through `setup_status`, so a
  code on disk could be relayed by a steered agent and a code checked against
  disk could be planted by one. The owner sees it through the decide response,
  the owner-only card event, and `GET /api/setup/cards[/{id}]`
  (`setup_channel.owner_view`). A gateway restart drops a live code.
- **The message.** `TelegramTransport` reads a private `/pair <code>` before
  `authorize` (see [messaging](messaging.md), Telegram `/pair`). A match adds
  the sender's numeric id to `telegram.allowed_user_ids` through
  `messaging.add_telegram_allowed_user` (appended inside the sidecar lock, then
  hot-applied so the live transport admits the sender before the reply) and
  commits the card with the sender's prompt-safe `@handle`. Each wrong code,
  from anyone, costs one of five attempts; the fifth fails the card with
  `pair_attempts`. Ten minutes unpaired expire it with `pair_timeout`.

## Job previews

A job's tool call that needs a person is a background approval (the gateway's
`_interactive_approval("cron")` callback): it names no slot, because an
unattended job is not a chat and no chat's trust may speak for it, and it is
declined when nobody answers within the unattended window
(`DashboardState._BACKGROUND_APPROVAL_TIMEOUT_SECS`). The gateway writes the run's
session key (`cron:<job id>`, or `cron:<job id>:<agent>` in an agent sequence) on
the approval record as `run_session`: provenance only, never a slot or a trust
lookup.

- **On the card.** `_preview_cron` starts a `setup_preview.ApprovalWatch` for the
  run. `GET /api/setup/cards/{id}/approvals` returns the pending approvals whose
  `run_session` names the job of THAT card's running preview, and nothing when
  none runs. Which job is held in process memory for the run, not read from the
  card store, which the agent's sandbox can write. The card polls it while the
  card is `working` and answers through `POST /api/approvals/{id}/{action}`
  (`approve`, `reject`), the one-shot path Notifications uses; nothing on the card
  records a standing grant. A request stays in Notifications too.
- **The verdict.** The cron service records a run whose approval was refused or
  expired as `ok` (only a security block counts against a run there). The watch
  counts how each of the run's approvals ended, and a `success` with any approval
  not given becomes `failure` with `reason: approval_not_given`. A preview that
  asked carries `approvals: {asked, allowed, rejected, unanswered, wait_secs}`. A
  failed preview keeps "Run a preview now" as the primary action.
- **Keeping it.** Nothing is auto-approved. The card says the job will ask again on
  every run, that the requests appear in Notifications, and how long one waits
  before it is declined. The only standing grants for a job are the operator's:
  the job's own `approval_mode: "auto"` (every tool, that job) and
  `hooks.auto_approve_sources` (every job).

## Signing in to AWS

A home is built in the owner's own AWS account, so the machine running Kiro Crew
needs an AWS CLI sign-in first, and the terminal asks nothing. When the card's
payload says AWS is not signed in, the card offers **Sign in to AWS**, which posts
`decision: "aws_signin"` (`setup_aws_signin.decide_signin`, owner-only and
governed like every decide):

1. It asks AWS who the payload's profile signs in as (`local_signin.detect`, the
   same read-only `sts get-caller-identity` the Hello's fact uses). An answer
   returns the card to `pending` signed in, and nothing is spawned.
2. It refuses with `aws_signin_remote` unless the owner's browser is on this
   machine: the decide request came straight from loopback with no forwarding
   header (`origin.is_direct_local_request`), the install shape is a desktop
   (`auth.shape.detect_shape`, the choice the Kiro sign-in's transport is made
   from; an SSH session or a container is not one), and this process can open a
   browser (a Linux host needs a display). The refusal's outcome carries
   `aws_signin: {state: "remote", command}`, the `aws login --remote` command to
   run in a terminal on that host; the card then offers Build.
3. Otherwise it runs `aws login [--profile P] --region R` as a child of the
   gateway, with stdin, stdout and stderr closed and a scrubbed environment, in
   its own session. The profile and region are the payload's, re-validated; the
   `default` profile passes no `--profile`, and `--region` also picks the region's
   sign-in endpoint. With no stdin `aws login` asks nothing: it opens the page
   and waits for its redirect, creating the profile if it does not exist. The
   card goes `waiting` with `aws_signin: {state: "waiting", expires_ts}`. One
   sign-in runs at a time (`aws_signin_busy`); an AWS CLI older than 2.32 is
   refused (`aws_cli_too_old`).
4. A watcher checks the child and asks AWS again every few seconds. When AWS
   answers, the card returns to `pending` with `aws_signed_in: true` and the
   account's last four digits in its OUTCOME, and its payload is recomputed now
   that AWS answers (`setup_flow.refresh_home_payload`): the account's own region,
   its plan and the size options, under a new hash (`setup_cards.replace_payload`,
   the one sanctioned payload change, for a `pending` card only). The owner sees
   the new card before Build; a click carrying the old hash is refused as
   `card_hash_mismatch`. A child that exits without a sign-in
   (`aws_signin_failed`) or ten minutes without one (`aws_signin_timeout`, the
   AWS CLI's own wait, which also leaves time to create an account first)
   returns the card to `pending` with the reason. So does a profile that already
   holds access keys, which `aws login` refuses at once with exit status 253
   (`aws_signin_profile_has_keys`). The card reaches the sign-in only when AWS did
   not answer, so this is a profile whose keys were revoked or expired. A retry
   cannot help, because the card's profile is fixed by its payload, so the card
   tells the owner to ask the chat for a home under a new profile, and the
   `crew-setup` skill proposes `kind: "home"` again with `profile: "kirocrew"`.

**No AWS account yet.** A card built on a machine with no AWS sign-in (and not
simulated) also carries `signup_url`, `signup_builder_id` and
`aws_cli_installed` in its payload (`setup_flow._home_payload`). `signup_url` is
`local_signin.signup_url(builder_id)`: AWS's Builder ID sign-up when this
machine's Kiro sign-in is exactly Builder ID (one bounded `kiro-cli whoami`,
`local_signin.kiro_signs_in_with_builder_id`), the plain sign-up for a social or
Identity Center sign-in, none, or an unknown answer. `aws_cli_installed` is
`local_signin.aws_cli_present()`. A signed-in or simulated card has none of the
three and runs no whoami. The card links "Create an AWS account" beside Sign in
to AWS, opening the page in a new tab through the safe-URL helper; it never
frames or proxies an AWS page. Following it switches the card to a local,
untimed "finish creating your account" state whose primary, "I've created it —
sign in", is the `aws_signin` decision above, and whose Back returns. A missing
AWS CLI adds one line linking AWS's install page; nothing is installed for the
owner.

`input.cancel` stops a sign-in in progress and returns the card to `pending`. The
child is stopped on timeout, on cancel, whenever the card leaves the sign-in, and
when the gateway exits. It lives in process memory only (the card store is
writable from the agent's sandbox, so nothing on disk names a process to
signal), which is also why a gateway restart leaves a card `waiting`: once it is
past `expires_ts`, the card offers Try again and the decide admits a fresh start.
The sign-in is audited as `setup_card.aws_signin`, with its outcome word only.

## The home's size

The card offers sizes and the owner picks one (`setup_cards.home_size_options`,
the tiers in `cloud/sizes.py`), measured with a real kiro-cli: the idle gateway is
1.3 GB, each open chat adds about 0.5 GB and stays alive, three chats plus a
sub-agent peak at 3.7 GB, and the on-box dashboard build peaks at 2.6 GB. The
two tiers below 8 GB run a slimmed home instead (the tier's `home_profile`; see
cloud.md "Slimmed homes"), and count on the launcher shipping the dashboard it
built (cloud.md "The prebuilt dashboard").

The "About" column is us-east-1; the card prices each option in the card's own
region (see [Prices per region](#prices-per-region)).

| Option | Tier | Shape | About | Offered on |
|---|---|---|---|---|
| Lite | `lite` | `t4g.small`, arm64, 2 vCPU, 2 GB | $14/month | both (`free_plan_ok`); it gives up meaning-based memory search (keyword only), dictation (no local speech-to-text), a warm first reply after a quiet spell, and runs a few things at once, which its card line says |
| Economy | `economy` | `t4g.medium`, arm64, 2 vCPU, 4 GB | $26/month | the paid plan; everything on, idle chats end after 30 minutes |
| Small | `small` | `t4g.large`, arm64, 2 vCPU, 8 GB | $51/month | the paid plan (its default) |
| Starter | `starter` | `m7i-flex.large`, x86_64, 2 vCPU, 8 GB | $72/month | the Free plan (its default; `free_plan_ok`: the Free plan's EC2 launches free-tier types only), and the paid plan only when it is no dearer than Small in the card's region |
| Standard | `light` | `t4g.xlarge`, arm64, 4 vCPU, 16 GB | $101/month | both; on the Free plan it is marked as needing the paid plan |

Which sizes each plan gets, and its default, is data: `setup_cards.HOME_PLAN_SIZES`
(per plan: the sizes and the preselected one) and `HOME_SIZE_OFFERS` (per size: a
plain label and a note code the dashboard words: `lite_tradeoffs`, `all_on`,
`free_plan_credits`, `few_chats`, `many_chats`; `lite_tradeoffs` and
`free_plan_credits` also name the credit's weeks when they are known). A size is a tier in `cloud/sizes.py` plus those
entries. A plan not known yet (not signed in, or an unreadable plan) gets the
Free plan's list, since a new account starts on it and Starter builds on every
plan. The options are sorted cheapest first, and each carries `key`, `label`,
`note`, `instance_type`, `vcpu`, `ram_gb`, `monthly_usd` and `free_plan_ok`; a
Free-plan size also carries `credits_usd` and `credit_weeks` when the plan's
remaining credits are known. The card heads each option with its label and
monthly cost ("Small · about $51/month") and preselects `size_default`. A card on
a machine not signed in to AWS adds a note that a brand-new account starts on the
Free plan.

Once AWS answers for the profile, `_home_payload` reads, side by side and
read-only (`cloud/local_signin.py`, through `aws.run_aws`): the region the account
can build in (`resolve_home_region`: `ec2 describe-availability-zones` in the
card's region, then the profile's, then us-east-2, eu-north-1 and ap-southeast-2,
moving on only after an access refusal; a new sign-up account refuses every region
but its own), which becomes the card's `region` and the build's; and the plan
(`account_plan`: `freetier get-account-plan-state` gives `{type: FREE|PAID|unknown,
credits_usd?, expires?}`; an account older than the plans answers
`ResourceNotFoundException` and is PAID). The plan is never changed from here.

### Asking for the region

When no region answers (`resolve_home_region` returns `""`), the payload carries
`region_unknown: true` and `region_choices`, the regions a home can be built in
(`local_signin.HOME_REGIONS`: the commercial regions every account has without an
opt-in), and its `region` becomes the one the profile names in `~/.aws/config` when
that is one of them. The card shows a native select of those regions, preselecting
`region`, under "AWS didn't say which region this account uses. Pick the one
shown in your AWS console." Its primary button is **Use this region**, the
`region` decision with `input.region`. `_decide_home_region` refuses a card that
does not ask (`invalid_decision`), a stale hash and a governance denial like any
decision, and a region that fails `setup_cards.validate_home_region` (the
`build_home` region shape and `HOME_REGIONS`: `home_region_not_offered`), before
anything is sent to AWS. It then probes that one region read-only
(`local_signin.probe_region`, one `ec2 describe-availability-zones`), reads the
plan again, and re-issues the card for the pick through `replace_payload`, under a
new hash and with that region's prices. A pick that answered drops
`region_unknown`, so the card shows Build. A pick that did not answer is kept as
the card's region, with the picker still shown and the recoverable error
`home_region_no_answer`; while the select still shows that region, the primary
button is Build, so an owner whose console shows it can build there (tests:
`test_home_region.py::TestNoRegionAnswers`, `TestTheRegionDecision`,
`SetupCardHomeRegion.test.tsx`).

### Prices per region

`setup_cards.monthly_estimate_usd(key, region)` is the instance's on-demand Linux
hourly price times 730 plus its disk at the region's gp3 price, rounded to whole
dollars. The prices are data in `cloud/sizes.py` (`ON_DEMAND_USD_PER_HR`,
`GP3_USD_PER_GB_MONTH`, via `region_prices`) for us-east-1 and a new account's
three home regions, from AWS's public price list (published 2026-09-25). A region
not in the table gets the us-east-1 figure (`PRICE_FALLBACK_REGION`), which the
card shows as "about" like every price. `home_size_options(plan, region)` prices
every option in the card's region, so the rule that Starter joins the paid plan's
list only when it is no dearer than Small is decided per region. Monthly figures,
instance plus disk:

| Size | us-east-1 | us-east-2 | eu-north-1 | ap-southeast-2 |
|---|---|---|---|---|
| Lite | $14 | $14 | $14 | $17 |
| Economy | $26 | $26 | $27 | $33 |
| Small | $51 | $51 | $53 | $65 |
| Starter | $72 | $72 | $77 | $90 |
| Standard | $101 | $101 | $104 | $128 |

The Build click posts `input.size`. `_chosen_home_size` refuses a size the card
did not offer (`home_size_not_offered`), a paid-plan size on the Free plan
(`home_size_needs_paid_plan`; the card links AWS's page on the plans), and a size
above the account's EC2 on-demand vCPU quota in that region (`vcpu_quota`,
Service Quotas `L-1216C47A`, read before anything is spent:
`home_vcpu_quota_low`, with a link to the Service Quotas page). A build that
fails on the account's spend limit is `home_spend_limit` rather than the generic
`home_build_failed`. `kirocrew start --home cloud` records no fallback region.
A build the card can no longer follow (its job unreadable or gone for
`_UNTRACKED_POLLS` polls in a row, or the watcher itself failing) is stopped
through the launch's own cancel event, whose worker rolls its stack back at the
next checkpoint, and the card fails with `home_build_untracked`
(`_stop_untracked_build`): a build nobody can see is one nobody would stop.

## The home's Kiro sign-in

The home signs in to Kiro with its OWN device-code sign-in; nothing is copied
from this machine, so each machine keeps its own session and refresh token (the
choice and why: RFC §6.8 rule 5). The build waits at "Sign in to Kiro" until the
owner approves the code, so that approval is made one click
(`dashboard/home_signin.py`):

- **The click records where the browser is.** Every `commit` on a home card
  records `private.browser_is_here`, the three-part check the AWS sign-in uses
  (`setup_aws_signin.browser_is_here`: a direct-local request, a desktop install
  shape, a browser this process can open). Build hands that answer, read off the
  card the click claimed, to its own watcher as `may_open`.
- **Only what this process issued opens.** The launch-job file lives under
  `run/`, which is `VISIBLE` in the sandbox, so an agent can rewrite its
  `signin` (swapping in its OWN device code, which the owner would then approve
  for the agent's session) and its `login_target.start_url`. So the launch
  worker records each prompt it publishes, with the target's start URL, in
  process memory (`launch_job._issue_signin` on both the launch and the retry
  path; `issued_signin(job_id)` reads it back, never from disk), and the watcher
  opens only when the file's `url` and `code` equal that record. The host check
  runs on the record's URL and start URL too, so a start URL rewritten in the
  file widens nothing. No record (another process, or this one after a restart)
  opens nothing. The card shows that record too (`setup_flow._shown_signin`),
  so a code planted in the file is neither opened nor shown while the worker
  runs; only after a restart, with no record, does the card show the file's
  copy, as it did before.
- **The page opens once.** When the build shows `signin: {url, code}`, the
  watcher opens the URL in the owner's browser (`cloud.login._open_browser`) if
  `may_open`, the home is not simulated, it is what this process issued, and the
  issued URL is HTTPS on a Kiro sign-in host (`*.awsapps.com`,
  `device.sso.<region>.amazonaws.com`, `*.kiro.dev`) or on the issued login
  target's own Identity Center start-URL host. kiro-cli prints the
  page with the user code in it (`verification_uri_complete`, which
  `parse_login_output` prefers), so a browser already signed in to Kiro needs
  one confirmation; the card keeps showing the code beside the link. A digest
  of the code is recorded before the open, so each code opens at most once,
  and a fresh code (a restarted sign-in) opens once more. A watcher not started
  by the click (a resume after a restart) never opens anything: the owner may
  have left the machine. Anything else leaves the link on the card.
- **One notice.** The chat that owns the card gets ONE `home_signin` system
  notice, meta `{kind, opened, card}`: the home waits for one click, and either
  its page opened in the browser or the link is on its card. It is not a model
  turn, so it costs no quota and raises no card (SC8), and it waits while that
  chat is mid-turn so it never lands between the rows of a reply. The dashboard
  draws localized copy keyed on `meta.opened` (`pages/chat/HomeSigninNotice.tsx`).
- **The card** says it is the home's own sign-in, that each machine keeps its
  own, and that a browser already signed in to Kiro needs one click.

The `private` flags (`browser_is_here`, `kiro_signin_opened`,
`kiro_signin_open_ok`, `kiro_signin_noticed`) only stop a repeat; the card store
is writable from the agent's sandbox, so none of them can start an open on its
own, and no URL comes from the card. The page that opens is always one the
worker in this process received from kiro-cli on the home.

**A build that finished unsigned.** The build finishes (`DONE`) even when its
sign-in step was skipped: the device code ran out unapproved, or a gateway
restart cut the wait short, leaving `signin_detected` false. Such a home's agent
cannot answer, so the watcher (`setup_flow._home_built`) does NOT offer Move in:
it sets phase `signin` and leaves the card `pending` with outcome `{ready: false,
needs_signin: true, steps}`, plus the `signin` link and code when the job still
holds them. The card's commit in that phase, **Sign the home in to Kiro**
(`_sign_home_in`), runs `handlers_cloud.restart_signin`, the same body as the
Instances hub's `POST /api/cloud/launch/{id}/signin/restart`, with every refusal
it has (already signed in, an unreadable identity, no crew to sign in on, a setup
or sign-in already running, no launch engine); a refusal is the card's error and
the card stays in phase `signin`. "Already signed in" goes straight to Move in.
Otherwise the card goes `waiting` and is watched again, with `may_open` from the
`browser_is_here` this click recorded, so the fresh code's page may open once in
the owner's browser. When that watch sees `DONE` with `signin_detected`, the card
moves to phase `move` as before. The phase is on the stored card, so a card left
at `needs_signin` across a gateway restart still offers the button. A simulated
home is never held here.

## Moving in

When a live home's build is done, the card's private record holds the EC2
instance id the launch registered in the Instances hub ("Added to Your crews").
Move in (`setup_move_in.move_in`) runs four steps, each on the card as it runs
(`outcome.move_steps`):

1. **Reach.** Needs `instances.enabled` and the tunnel manager the gateway starts
   at boot, the same gate every `/api/instances` route applies. When Remote Crew
   is off, the owner's Move in click is the opt-in: it sets `instances.enabled`,
   restarts the gateway once, and returns the card to `pending` with
   `move_in_restarting`, so the owner presses Move in again once the chat is back.
   Then it finds the registry record whose `ssm_target` is that instance id and
   connects it (`SshTunnelManager.connect`).
2. **Pack.** `portability.create_export_zip`: memory (every store), schedules,
   skills, workspace, plan memory, hooks, notifications, crew teams,
   `persona/SOUL.md` and `USER.md`, and `config.json`. The vault, `.env`, the SEL
   key and connection grants are never in it.
3. **Chat.** The card's chat goes to the home through the session-transfer path
   (`build_transfer_bundle_async`, the publication hold, then
   `SshTunnelManager.send_session_bundle`), as a copy: the chat here is untouched.
   The home answers with the copy's slot key, which the card records.
4. **Carry.** A schedule reports to the chat its `session_key` names
   (`dashboard:<slot>`), and this chat's key names nothing on the home. So every
   job in the archive's `crons.json` whose `session_key` is this chat's
   (`card.session_key`, `dashboard:<card.slot>`), enabled or not, is pointed at
   `dashboard:<copy key>` (`rebind_jobs_to_chat`); only the archive changes, and
   the jobs here keep their own key. Then the enabled schedules the archive
   carries that run a prompt are switched off here (`enable_job_async(id, False)`,
   so disabled, not deleted), and only then is the archive posted to the home's
   `POST /api/portability/import?mode=merge` through
   `SshTunnelManager.proxy_request`, which keeps the tunnel credential inside the
   manager. A schedule that runs a command or a script stays enabled here: the
   import pauses it on the home and the export carries no script. If the home
   does not confirm the import, the switched-off schedules come back on and the
   card returns to `pending` with the reason. A schedule the home rejected, or
   every schedule when the home could not read its own schedule list, comes back
   on here and is listed as kept. The merge restores `config.json` only on a
   home that has none, so a home usually keeps its own settings; the result says
   so. A home that answered the chat step without a key gets the archive
   unchanged.

The chat goes before the carry so the archive can name the copy's key: a moved
job never runs on the home under an owner key that names no chat there. SC5 (a job
runs on exactly one crew) fixes the order inside step 4: the home's cron service
loads an imported job on its next sync, so switching the local copies off after the
import would leave both running for a moment. The card's `private.move` records
each finished step (the chat's copy key, then the carry), so pressing Move in
again never sends the chat or the archive twice: a retry after a failed carry
packs afresh, keeps the recorded copy key and carries again. A failed chat step
has moved nothing else. A reply lost after the home applied the archive is the one
case that runs a schedule on both crews, until the owner presses Move in again and
the home's merge skips the names it already has. A gateway stopping mid-move turns
the schedules back on and returns the card to `pending`.

The committed outcome names the home (`home.instance_id`, `home.name`,
`home.remote_key`), `jobs_moved` (off here), `jobs_kept_here` with a reason,
`jobs_follow_chat` (the jobs now owned by the chat's copy), `carried` (the home's
import summary items), `settings_moved`, and `reenter`: the vault's secret names,
the credential file's credential names, and the curated connections holding a
grant here, never a value. The `[Setup card result]` turn tells the agent where
the chat now lives, which schedules report to it there, and what the user enters
again on the home. Every step is audited as `setup_card.move_in`.

## Governance

`capabilities.setup` (`platform/governance.py` `SCOPE_CATALOG`, default on) gates
every proposal and every commit; its inner `kinds` ruleset checks the card kind
as the item, so a fleet can keep cards while refusing, say, `service`. Cron cards
additionally pass `capabilities.cron` (`mcp_cron._vet_cron_capability_governance`).
The core MCP server is auto-approved, so the card is the consent step and these
checks run inside the flow, not at the permission gate.

## The first-run session

`dashboard/server.py` calls `setup_flow.ensure_first_run_session` after the
session restore. It creates one pinned slot titled for the first run, records it
in the state file, and appends the privacy card — only when the install is not
onboarded, the privacy flag is unset, no slot is live and no session exists on
disk. It is idempotent across restarts. `_theme_payload` reports
`first_run_slot`, which the SPA uses to keep the classic chapters from opening
by themselves; `/onboarding` still opens them.

On a desktop-width page load that opens on the first-run chat before
graduation, the dashboard starts with the nav rail collapsed to its icons and
the session list hidden (`hooks/useFirstRunLayout.ts`). The rule is decided once
per load and never persisted: the rail and sessions toggles write `mc-nav` and
`mc-sidebar-pinned` as they always do, and a stored value wins. Any other load,
including the main chat after graduation, keeps the stored or default layout.

Committing the privacy card dispatches the `[First run]` kickoff turn
(`FIRST_RUN_PREFIX` in `dashboard/state.py`, `injectKind: "first_run"`), whose
text carries facts the gateway gathered (other agents detected, curated
connections, whether the service is installed, and where the crew lives) and the
`$crew-setup` token, so the skill body is expanded into that turn.

Where the crew lives is a step of its own, asked in the chat, never in the
terminal. Before that kickoff, `_offer_home_step` shows a home card with payload
`offer: true` (`HOME_STEP_KEY`) on every first run: "Where should your crew
live?". Its payload is the ordinary home card's (`_home_payload`: one read-only
AWS reachability check; the profile's region from `local_signin.configured_region`,
else `HOME_DEFAULT_REGION`), so a signed-in machine sees the account's last four
digits, region and monthly cost, and a signed-out one gets the sign-in and
account-creation path (see [Signing in to AWS](#signing-in-to-aws)). Declining it
keeps the crew on this machine. The kickoff fact (`_home_step_fact`) tells the
Hello to point to the card in one sentence, not to ask again in prose, and to
guide the owner through the card's AWS steps when they choose the cloud. The step
card is the gateway's, so it does not count toward the agent's card budget. A
`--home cloud` answer shows the same card without the step framing;
`--home here|later` shows none. An earlier prototype asked only in the Hello's
prose; a tester missed it next to the first card, which is why it is a card.

## Guardrails

Four guardrails keep the first run from running away or going quiet. Each one
that speaks posts one deterministic system notice in the first-run chat. The
notice's English content is the fallback text; the dashboard draws localized copy
keyed on `meta.kind` and `meta.reason` (`components/setup/SetupGuardrailNotice.tsx`),
and every notice offers classic setup (`/onboarding`).

| Guardrail | Trigger | What the user sees | Then |
|---|---|---|---|
| Card budget | `CARD_BUDGET_BEFORE_FIRST_JOB` proposals without a kept job | nothing; `propose` tells the model to stop proposing | the budget is lifted by the first kept job |
| Stall | a first-run turn whose progress markers have not moved for `FIRST_RUN_STALL_SECS` (90 s) with nothing to wait on | `setup_stalled`, `reason: no_output`: stop the reply and send again, or use classic setup | at most one per turn |
| Kickoff | the `[First run]` kickoff ends with no reply (and no retry or queued turn follows it), or cannot be dispatched | `setup_stalled`, `reason: kickoff_failed`, with Try again | Try again posts `POST /api/setup/first-run/retry` |
| Quota | a first-run turn whose last word is the `usage_limit` error row | `setup_quota`: the allowance ran out; cards already shown and classic setup still work; the chat keeps its place | `propose` refuses new cards until a turn in that chat lands a reply |

The stall verdict is the session-health classifier's
(`dashboard/session_health.py`: `snapshot_state` and
`SessionHealthMonitor.classify_slot`, run with a private monitor on the shorter
window). It uses the same progress markers and wait reasons
`GET /api/sessions/health` reports, so an open approval, a pending question, a
running child, a parked `wait` or a recovery in flight is never called a stall.
The watch is armed once per top-level turn at the top of `chat_runner._run_chat`.
It returns at once for any chat that `setup_flow` did not record as the
first-run chat (`setup_guardrails.track`, weakly keyed by the gateway state). It
stops once the chat becomes the main chat. It samples every `_WATCH_POLL_SECS`
and judges the turn's rows when the turn's task ends. The ACP layer's own
stale-turn cutoff applies only after text has streamed, and its tool-stall cutoff
only while a tool call is open, so a turn that has produced nothing at all is
otherwise bounded only by the hours-long turn ceiling. That silence is the case
this guardrail covers.

The quota verdict comes from the row kind `chat_runner._terminal_error_meta`
sets from the provider's raw frame (`AcpError.usage_limit`), never from prose. A
turn whose model fallback answered after the limit is not an episode, and a
second failing turn in the same episode posts nothing more. The pause lives in
memory and can only make a proposal refuse. The retry route refuses with
`slot_not_found`, `privacy_not_acked`, `turn_running` or `kickoff_answered` (an
assistant reply after the last `first_run` inject row). The retried kickoff
carries user provenance for the same reason the first one does (SC8). None of
these reads or writes a keystone file (SC3), and the first-run state file only
picks which chat is watched (SC6).

## The main chat

When the first-run chat's first cron card is kept, `setup_flow.graduate` makes
it the **main chat**: it records `main` in the first-run state, renames the slot
after the agent (`agent.bot_name`, an explicit title the auto-titler leaves
alone), keeps it pinned, marks the `main` stage, and appends a `main_chat`
system notice. `_theme_payload` reports `main_slot`; `kirocrew start` lands on
it. Like the rest of the state file, the marker is presentation only.

Any other dashboard chat can be made the main chat later: "Make this my main
chat" in the session menu (sidebar row and chat header) posts
`POST /api/setup/main-chat {slot}` (owner-only; `setup_flow.make_main_chat`),
which moves the marker, pins the chat and appends a `main_chat` notice. Only a
chat whose key starts with `chat-` and that did not come from a channel is
eligible (`slot_not_eligible`, 409). The web and desktop apps land, with nothing
remembered and no `?sid=`, on the main chat, then the first-run chat, then the
first row. "Ask in main chat" on a job and "Ask about this chat in main chat" on a
session pre-fill the main chat's composer, unsent.

In the main chat only, every top-level turn carries a `[CREW OVERVIEW]` block
(`setup_flow.crew_overview`, attached in `chat_runner` beside the theme
persona): other live chats with their status (working, waiting on the user,
idle), setup cards open anywhere, enabled jobs in due order, and the home's
state. Titles are flattened (no brackets, one line, bounded) before quoting, and
the block is capped at `OVERVIEW_MAX_CHARS`. It carries what `list_sessions` and
`setup_status` already return, so it widens nothing.

The main chat is told when a chat it handed work to finishes. When a chat whose
`_created_by` (stamped by `session_create`) is the main chat ends a turn and is
idle (nothing queued, no approval or question waiting, no plan or sub-agent still
going), `dashboard/handoff_notice.py` posts one `handoff_done` system notice in
the main chat, meta `{kind, slot, title, outcome}`, the title flattened and
bounded as in the overview. `outcome` is `done` when the turn replied and `error`
when it ended on an `error` row with no reply. A turn with a `stop_event` row
posts nothing: every Stop press and `session_stop` writes one, so whoever stopped
it already knows. The notice runs no model turn, so it costs no quota and raises
no card (SC8). It is checked at two cycle ends, `note_cycle_end` in
`chat_runner._finish_queue_cycle` after `chat_done` and `note_controller_end`
where `_stage_loop` releases the slot (run once the plan's task has ended, since
the controller keeps the slot reserved until then; a plan paused on the user is
not reported done). For a chat nobody created, both return without reading the
disk. There is at most one notice per turn of that chat, none while the main
chat's newest row is already the same notice, and none for the main chat itself.
A notice due while the main chat is mid-turn or mid-plan is held until its next
cycle end, and the hold is mirrored to `handoff_owed` in the first-run state file
(at most `_MAX_HELD` per chat), so a restart in between delivers it:
`restore_held` runs in `server.py` after the session restore, drops notices held
for a chat that is no longer the main chat, bounds the title again, and dedupes
against the newest row. The dashboard draws the notice with Open "title" and,
for `done` only, Ask for the result, which sends `What did "title" find?` as an
ordinary user message; the main chat then reads the chat with
`session_read_message`. An `error` notice is a warning with Open only. Like the
marker, presentation only.

The first-run chat runs on the `kirocrew-main` agent spec (`slot.agent`,
`agent_files.MAIN_CHAT_AGENT_NAME`), because handing long work to its own chat
needs the session tools, which live on the opt-in `kirocrew-dashboard` server the
default agent never mounts. `kirocrew-main` is the default agent's spec on disk
plus that server, with only `session_create` and `session_read_message`
auto-approved; `session_send` and `session_stop` stay behind the approval gate, and
a governance ceiling on the server withholds both grants. Every other chat keeps
the default agent, and a chat the main chat creates without naming an agent starts
on the default agent rather than inheriting `kirocrew-main`. Context, skills and
model resolution treat it as the default agent (`agent_files.PRIMARY_AGENT_NAMES`),
so the persona files and the skill catalog still reach this chat. The spec, its
freshness gate and what each harness makes of it:
[agent-spec-fields](../../../src/kiro_crew/docs/agent-spec-fields.md) and the
[agent host contract](agent-host-contract.md) §5. On the Claude harness the server
mounts but no grant reaches the harness, so all four verbs prompt; on codex,
OpenCode, goose, Pi and DeepSeek it is not mounted, exactly as for the conductors,
so the main chat there has no session tools.

A second pending card is refused while one waits for the user's decision
(the home card excepted), so the chat asks for one decision at a time.

## The first week

`dashboard/first_week.py` posts at most one tip a day in the main chat for the
seven days after graduation (the `job_kept` stage). A tip is a fixed system
notice (`first_week_tip` in `dashboard/system_notices.py`), never a model turn
and never a card (SC8). `next_tip` picks the first tip not yet shown whose
condition still holds (no connection, not staying on, no channel, one job, no
SOUL.md, then the skill tip), and only when the user sent a message in the main
chat within `ACTIVE_WITHIN_SECS`, in local daytime, and at least
`TIP_MIN_GAP_SECS` after the last tip. Two tips in a row with no reply, the
phrase "no more tips" in the main chat (`note_user_message`, called from
`api_chat`), or the end of the week stops them. The gateway starts the loop at
boot (`server.py`); an install without a first run returns at once. The
bookkeeping is `first_week` in the first-run state file, presentation only.

## Pasted secrets

`api_chat` passes every message through `capture_pasted_secrets` after the
identity gates and before any branch queues, stores or sends it. Credentials the
shared detectors (`security.redaction.redact_credentials_with_records`) find are
replaced by `secret://NAME`; for the owner the value is stored in the vault
under a name derived from the detector rule (reusing a name that already holds
the same value). AWS keys and private keys are removed and never stored. A
`secret_captured` system notice (`dashboard/system_notices.py`) says what
happened.
