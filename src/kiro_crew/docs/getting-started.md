# Getting Started with Kiro Crew

Kiro Crew is an autonomous AI agent layer that runs on your own machine on top of
the kiro-cli (KiroACP) backend. It adds persistent memory, scheduled jobs,
background subagents, self-learning, and multi-session orchestration, and you
talk to it from a web dashboard, from the terminal, or from messaging channels
like Slack, Discord, and Telegram.

## Prerequisites

| Requirement | Needed for | Floor |
|-------------|------------|-------|
| **Python** + pip | Backend | `>= 3.12` |
| **Node.js** + npm | Building the dashboard from source | `>= 22.12` (24 LTS recommended) |
| **`kiro-cli`** | Driving the LLM | Required, on your `PATH` |

Node is only needed to *build* the dashboard. The prebuilt wheel, the macOS DMG,
and the Linux AppImage all ship the dashboard already bundled, so if you install
one of those you need neither Node nor a compiler.

**Platforms: macOS, Linux, and Windows.** Windows runs natively from a Python
source install and is launched as `python -m kiro_crew gateway`.

Embeddings need no setup step. They run in-process, and on first start the
gateway downloads the embedding model (about 610 MB) in the background and
verifies it against a pinned sha256. Until it lands, memory search falls back to
keyword search and picks up semantic search automatically with no restart.

## Installation

### One command, from install to an open chat (fastest)

```bash
curl -fsSL https://download.crew.kiro.dev/start.sh | sh
```

`start.sh` runs the `cli.sh` installer below unchanged, then `kirocrew start`:
it checks that kiro-cli is installed and signed in, starts the gateway in the
background and opens the chat in your browser. On a host with no browser it
prints the sign-in link instead. It passes `cli.sh`'s flags through and adds
`--no-browser`, `--foreground` and `--skip-install`. It asks nothing: setup
happens in the chat (see [Your first chat](#your-first-chat)). On Windows,
`irm https://download.crew.kiro.dev/start.ps1 | iex` does the same with the
signed desktop installer.

### Prebuilt wheel from the release CDN

```bash
curl -fsSL https://download.crew.kiro.dev/cli.sh | sh
```

This installs a signed, sha256-verified `kirocrew` wheel and starts nothing. `stable` is the
default channel; pass `--channel insider` or `--channel nightly` to track a
faster one, or `--version X.Y.Z` to pin an exact release. The installer uses
`pipx` when available, otherwise it creates a managed venv and symlinks
`kirocrew` into `~/.local/bin`.

`pip install kirocrew` alone does **not** work: Kiro Crew is not published to
PyPI. To install with pip directly, point it at a release channel's index:

```bash
pip install --pre kirocrew \
  --extra-index-url https://updates.crew.kiro.dev/feed/stable/simple/
```

`--extra-index-url` (not `--index-url`) is required, because the channel index
carries only `kirocrew` and pip still needs PyPI to resolve its dependencies.

### From a source checkout

```bash
git clone https://github.com/kirodotdev/KiroCrew.git
cd KiroCrew
bash setup.sh
```

`setup.sh` builds the dashboard and the backend into a `.venv`, puts `kirocrew`
on your PATH and ends in `kirocrew start`, asking nothing. `--no-start` stops
before the chat opens. To do the same by hand:

```bash
cd website && npm install && npm run build && cd ..
pip install -e ".[voice]"       # [voice] adds the optional speech-to-text extras
```

The dashboard has to be built before the backend install, because the built
`website/dist` is staged into the package and served by the gateway. `make
build` does both steps plus a `.venv`.

### Agent backend: `kiro-cli` (required)

`kiro-cli` is the only provider (`agent.provider` is fixed to `acp`). Install it
per its own docs, make sure the binary resolves on your `PATH`, and log in:

```bash
kiro-cli login
```

On the first dashboard launch, the Set up Kiro page walks through installing the
CLI and completing sign-in.

## Your first chat

On a fresh install, `kirocrew start` opens a first-run chat in which the agent
sets Kiro Crew up with you. Every change it wants is a card you click; nothing
changes until you do.

1. **Privacy** comes first, before the agent says anything.
2. **Where your crew lives.** Keep it on this machine, or build a home in the
   cloud in your own AWS account. The card lists the sizes your account can
   build, cheapest first, with a monthly cost for your region, and you pick
   one; it can sign you in to AWS, or link AWS's sign-up if you have no
   account. The home builds in the background and asks for its own one-click
   Kiro sign-in; when it is ready, **Move in** hands the crew and this chat
   over.
3. **The rest of setup** is a conversation: the agent offers to bring over
   another agent it found on this machine, connects one service such as GitHub,
   and runs a first job once so you see its output before you keep it.

Keeping that first job makes this chat your **main chat**: the product opens
on it, it can see and start your other chats, and for a week it posts at most
one short tip a day. Paste a token into any chat and it is moved into the vault
before the agent sees it.

## First-Time Setup in the terminal

```bash
kirocrew setup
```

The terminal alternative to the first-run chat. This interactive wizard detects
`kiro-cli` on your PATH, saves the project
directory so Kiro Crew works from any working directory, installs the agent
config to `~/.kiro/agents/kirocrew.json`, and walks through the workspace
directory, timezone, dashboard URL, and the `http://kirocrew.localhost:5476`
custom domain. It configures no messaging channels: connect them after setup
from the dashboard, or run `kirocrew setup --slack` for the guided Slack setup.

To browse, install the Playwright agent CLI (needs Node.js 20 or newer):

```bash
npm install -g @playwright/cli@latest
playwright-cli install-browser              # --with-deps on Debian/Ubuntu only
playwright-cli install --skills agents --global
```

`--with-deps` installs OS libraries through `apt` and needs root. Playwright
implements it for apt alone, so on Fedora, RHEL, CentOS or Amazon Linux it
misfires against Ubuntu package names; install the libraries with your own
package manager instead. The Settings → Browser install button handles this
per-distribution and prints the command to run when it needs root.

Having `playwright-cli` on your `PATH` is what makes browsing available, so
uninstalling it is how you take the capability away. Note that it covers
`playwright-cli attach --extension`, which drives your own running Chrome with
the sessions you are logged into. The dashboard's **Browser** panel shows the
live session and lets you take over with real mouse and keyboard, which is how
you complete a CAPTCHA or a 2FA prompt.

Use `kirocrew setup --agent-only` to reinstall just the agent config and skip
the other wizard steps. `--electron-only` installs only the desktop app (macOS),
and `--clean` treats the run as a fresh install rather than merging MCP servers
and tools from an existing config.

### Messaging channels (optional)

The default wizard configures no messaging channels — the dashboard and CLI need
none. Two channels have a guided terminal setup: `kirocrew setup --slack`, and
`kirocrew setup --whatsapp`, which reports the optional `whatsapp` extra and the
pairing state before enabling the channel. Both are ignored with `--agent-only`.

`--slack` prompts for:

- `SLACK_APP_TOKEN` starts with `xapp-`
- `SLACK_BOT_TOKEN` starts with `xoxb-`
- `KIROCREW_OWNER_ID` is your Slack user ID (starts with `U`)

Use the user ID from the workspace where the bot is installed: your user ID is
different in every Slack workspace. Only the owner is authorized to interact
over Slack.

These are stored in `~/.kiro/crew/.env`.

Every other messaging channel is connected from the dashboard — the roster is in
[the documentation index](index.md#chat-channels), and each channel has its own
doc there.

## Starting Kiro Crew

### The one command

```bash
kirocrew start
```

Checks kiro-cli (installed, signed in), reuses a running gateway or starts one
in the background, and opens the chat in your browser. `--foreground` runs the
gateway in this terminal instead, `--no-browser` prints the sign-in link, and
`kirocrew stop` stops a background gateway.

### Gateway mode (dashboard + messaging channels)

```bash
kirocrew gateway
```

This starts the full server: web dashboard, listeners for every configured
messaging channel, cron scheduler, heartbeat, and update checker. The dashboard
is at `http://localhost:5476`.

### Chat mode (CLI only)

```bash
kirocrew chat                            # interactive REPL
kirocrew chat -m "what's the weather like?"   # single message
```

Lightweight mode: no messaging channels, no dashboard, just a terminal
conversation.

## Verifying Your Setup

```bash
kirocrew doctor
```

Reports the resolved platform edition, the `kiro-cli` binary and login state,
the project directory, the agent config and its MCP entries, Slack credentials,
gateway status, and the embedding runtime and model. It repairs missing MCP
entries where it can, and prints a specific fix hint for anything it cannot.

## Updating

```bash
kirocrew update
```

For a source checkout this updates the checkout and rebuilds it. Restart the gateway with `kirocrew restart` to use the new version. Clicking "Update Available" in the dashboard topbar runs the same path.

## Running in the Background

```bash
kirocrew service install     # launchd LaunchAgent on macOS, systemd unit on Linux
kirocrew service status
kirocrew service uninstall
```

The service survives an SSH disconnect, restarts on crash, and starts on boot.
On Linux the install prompts for `sudo` once to write the unit; the gateway
itself then runs as your own user. macOS needs no `sudo`.

Tail its output with `kirocrew logs -f`.

## Dev Mode

```bash
export KIROCREW_HOME=.kirocrew-dev
export KIROCREW_PORT=6777
kirocrew gateway
```

`KIROCREW_HOME` gives the instance its own data directory, so dev memory, crons,
and sessions never touch your real `~/.kiro/crew`. `KIROCREW_PORT` lets a second
gateway run alongside the first. The two together are what make it safe to run
several Kiro Crew instances at once.
