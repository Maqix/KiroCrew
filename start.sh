#!/bin/sh
# ──────────────────────────────────────────────────────────────────────
# Kiro Crew in one command: install it, start it, open the chat.
#
#   curl -fsSL https://download.crew.kiro.dev/start.sh | sh
#   curl -fsSL https://download.crew.kiro.dev/start.sh | sh -s -- --channel insider
#   curl -fsSL https://download.crew.kiro.dev/start.sh | sh -s -- --no-browser
#
# Installs Kiro Crew with cli.sh, then runs `kirocrew start`, which checks the
# agent harness (kiro-cli by default), starts the gateway and opens the
# first-run chat in the browser -- or, on a host with no browser, prints a
# sign-in URL with a QR code or an `ssh -L` hint.
#
# This is not a second installer. cli.sh is downloaded from the same CDN and
# run unchanged, so the signed-manifest verification, its pinned public key and
# the managed Python all stay cli.sh's own; a trust root copied here would
# drift from the one cli.sh verifies against.
#
# The whole body is one function called on the LAST line, so a download cut
# short anywhere runs nothing instead of half a script.
#
# Options / env:
#   --no-browser                         print the sign-in URL instead of
#                                        opening a browser
#   --foreground                         run the gateway in this terminal
#                                        (Ctrl-C stops it) instead of in the
#                                        background
#   --skip-install                       use the kirocrew already on PATH;
#                                        installs only when there is none
#   --channel <nightly|insider|stable>   passed to cli.sh (env KIROCREW_CHANNEL)
#   --version <X.Y.Z>                    passed to cli.sh
#   --cdn <base-url>                     passed to cli.sh, and where cli.sh is
#                                        fetched from (env KIROCREW_CDN_BASE)
#   --managed-python / --system-python   passed to cli.sh
#   KIROCREW_HOME                        the data home (default ~/.kiro/crew)
# ──────────────────────────────────────────────────────────────────────
set -eu

_ks_die() { echo "kirocrew-start: $*" >&2; exit 1; }

_ks_usage() {
  cat <<'EOF'
Kiro Crew in one command: install it, start it, open the chat.

  curl -fsSL https://download.crew.kiro.dev/start.sh | sh
  curl -fsSL https://download.crew.kiro.dev/start.sh | sh -s -- --channel insider

Installs Kiro Crew with cli.sh (downloaded from the same CDN and run unchanged,
so it verifies the signed manifest exactly as a plain cli.sh install does),
then runs `kirocrew start`: it checks the agent harness, starts the gateway and
opens the first-run chat in your browser. On a host with no browser it prints
the sign-in URL instead.

Options:
  --no-browser                         print the sign-in URL instead of opening
                                       a browser
  --foreground                         run the gateway in this terminal
                                       (Ctrl-C stops it)
  --skip-install                       use the kirocrew already on PATH;
                                       installs only when there is none
  --channel <nightly|insider|stable>   passed to cli.sh
  --version <X.Y.Z>                    passed to cli.sh
  --cdn <base-url>                     passed to cli.sh, and where cli.sh is
                                       fetched from (env KIROCREW_CDN_BASE)
  --managed-python / --system-python   passed to cli.sh
EOF
}

# The data-home marker write cli.sh uses, for the same reason: the data home is
# agent-writable, so a plain `>` could follow a planted symlink out of it.
# mktemp creates a fresh regular file, a symlink at the destination is removed
# first, and a directory there is refused. Bookkeeping only, so a failure warns
# and the start continues.
_ks_write_marker() {
  _ks_dest="$_KS_DATA_HOME/$1"
  if ! mkdir -p "$_KS_DATA_HOME"; then
    echo "kirocrew-start: could not create $_KS_DATA_HOME; not recording $1" >&2
    return 0
  fi
  if [ -L "$_ks_dest" ]; then
    rm -f "$_ks_dest"
  fi
  if [ -d "$_ks_dest" ]; then
    echo "kirocrew-start: a directory occupies $_ks_dest; not recording $1" >&2
    return 0
  fi
  if ! _ks_mtmp="$(mktemp "$_KS_DATA_HOME/.marker.XXXXXX")"; then
    echo "kirocrew-start: could not record $1 in $_KS_DATA_HOME" >&2
    return 0
  fi
  printf '%s\n' "$2" > "$_ks_mtmp"
  mv -f "$_ks_mtmp" "$_ks_dest"
}

# Where a fresh install put `kirocrew`: PATH first (what typing the command
# would run), then the two locations cli.sh installs to when PATH does not
# include them yet -- the managed venv's ~/.local/bin link, and pipx's bin dir.
_ks_find_kirocrew() {
  if command -v kirocrew >/dev/null 2>&1; then
    command -v kirocrew
    return 0
  fi
  if [ -x "$HOME/.local/bin/kirocrew" ]; then
    printf '%s\n' "$HOME/.local/bin/kirocrew"
    return 0
  fi
  if command -v pipx >/dev/null 2>&1; then
    _ks_pipx_bin="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)"
    if [ -n "$_ks_pipx_bin" ] && [ -x "$_ks_pipx_bin/kirocrew" ]; then
      printf '%s\n' "$_ks_pipx_bin/kirocrew"
      return 0
    fi
  fi
  return 1
}

_ks_cleanup() {
  if [ -n "${_KS_TMP:-}" ]; then
    rm -f "$_KS_TMP"
  fi
}

main() {
  _KS_BASE="${KIROCREW_CDN_BASE:-https://download.crew.kiro.dev}"
  _KS_DATA_HOME="${KIROCREW_HOME:-$HOME/.kiro/crew}"
  _KS_TMP=""
  _ks_no_browser=""
  _ks_foreground=""
  _ks_skip_install=""

  # One pass that keeps cli.sh's flags as the positional parameters (each is
  # taken off the front and re-appended at the back) and records this
  # script's own flags aside. cli.sh exits 2 on an argument it does not know,
  # so none of ours may reach it. The accepted spellings are cli.sh's own.
  _ks_n=$#
  while [ "$_ks_n" -gt 0 ]; do
    _ks_arg="$1"
    shift
    _ks_n=$((_ks_n - 1))
    case "$_ks_arg" in
      --channel|--version|--cdn)
        [ "$_ks_n" -gt 0 ] || _ks_die "$_ks_arg needs a value"
        _ks_value="$1"
        shift
        _ks_n=$((_ks_n - 1))
        [ -n "$_ks_value" ] || _ks_die "$_ks_arg needs a value"
        if [ "$_ks_arg" = "--cdn" ]; then
          _KS_BASE="$_ks_value"
        fi
        set -- "$@" "$_ks_arg" "$_ks_value"
        ;;
      --channel=*|--version=*)
        set -- "$@" "$_ks_arg"
        ;;
      --cdn=*)
        _KS_BASE="${_ks_arg#*=}"
        set -- "$@" "$_ks_arg"
        ;;
      --managed-python|--system-python)
        set -- "$@" "$_ks_arg"
        ;;
      --no-browser) _ks_no_browser=1 ;;
      --foreground) _ks_foreground=1 ;;
      --skip-install) _ks_skip_install=1 ;;
      -h|--help)
        _ks_usage
        exit 0
        ;;
      *)
        echo "kirocrew-start: unknown argument '$_ks_arg' (see --help)" >&2
        exit 2
        ;;
    esac
  done
  _KS_BASE="${_KS_BASE%/}"

  if [ -n "$_ks_skip_install" ] && _ks_found="$(_ks_find_kirocrew)"; then
    echo "Using the installed kirocrew at $_ks_found (--skip-install)."
  else
    command -v curl >/dev/null 2>&1 || _ks_die "curl is required"
    trap '_ks_cleanup' EXIT
    trap '_ks_cleanup; exit 129' HUP
    trap '_ks_cleanup; exit 130' INT
    trap '_ks_cleanup; exit 143' TERM
    _KS_TMP="$(mktemp "${TMPDIR:-/tmp}/kirocrew-cli.XXXXXX")" \
      || _ks_die "could not create a temporary file"
    echo "Downloading the Kiro Crew installer from $_KS_BASE/cli.sh ..."
    curl -fsS --proto '=https' --tlsv1.2 -o "$_KS_TMP" "$_KS_BASE/cli.sh" \
      || _ks_die "could not download $_KS_BASE/cli.sh"
    # stdin is /dev/null: under `curl | sh` it is the pipe this script arrived
    # on, and nothing cli.sh runs has a question to ask there.
    _ks_rc=0
    sh "$_KS_TMP" "$@" </dev/null || _ks_rc=$?
    _ks_cleanup
    _KS_TMP=""
    trap - EXIT HUP INT TERM
    if [ "$_ks_rc" -ne 0 ]; then
      echo "kirocrew-start: the installer (cli.sh) failed with exit status $_ks_rc" >&2
      exit "$_ks_rc"
    fi
    _ks_write_marker install-origin start
    _ks_found="$(_ks_find_kirocrew)" \
      || _ks_die "kirocrew was installed but cannot be found; open a new shell and run: kirocrew start"
  fi

  echo ""
  # Under `curl | sh` stdin is the pipe, so kiro-cli's own sign-in (a device
  # code, a browser hand-off) could never run. Hand `kirocrew start` the
  # terminal when there is one to hand; it asks nothing itself.
  if (exec </dev/tty) 2>/dev/null; then
    exec "$_ks_found" start ${_ks_no_browser:+--no-browser} ${_ks_foreground:+--foreground} </dev/tty
  fi
  exec "$_ks_found" start ${_ks_no_browser:+--no-browser} ${_ks_foreground:+--foreground}
}

main "$@"
