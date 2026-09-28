"""``start.sh``: the one-command install that ends in an open chat.

start.sh must stay a thin wrapper: it downloads the live ``cli.sh`` and runs it
unchanged (so the signed-manifest trust root has exactly one copy), records that
the install came through it, and execs ``kirocrew start``. These tests run the
real script under a fabricated ``PATH`` whose ``curl`` serves a fake ``cli.sh``
from a fixture directory; the fake cli.sh records its argv and installs a stub
``kirocrew`` that records ITS argv. Nothing touches the network or the host's
own install. POSIX shell, so the suite skips on native Windows.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from installer_test_helpers import run_bounded

REPO_ROOT = Path(__file__).resolve().parents[1]
START_SH = REPO_ROOT / "start.sh"

pytestmark = pytest.mark.skipif(os.name == "nt", reason="start.sh is POSIX shell")

# The coreutils start.sh itself calls. The PATH is ONLY these plus the fakes, so
# no real curl, cli.sh or kirocrew can leak into a run.
_REAL_TOOLS = ("sh", "cat", "chmod", "mkdir", "mktemp", "mv", "rm")


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


class _Harness:
    """A throwaway HOME, data home, TMPDIR and PATH for one start.sh run."""

    def __init__(self, tmp_path: Path, *, cli_sh_exit: int = 0) -> None:
        self.root = tmp_path
        self.tools = tmp_path / "tools"
        self.markers = tmp_path / "markers"
        self.fixtures = tmp_path / "fixtures"
        self.home = tmp_path / "home"
        self.data_home = tmp_path / "data-home"
        self.tmpdir = tmp_path / "tmp"
        for directory in (self.tools, self.markers, self.fixtures, self.home, self.tmpdir):
            directory.mkdir(parents=True)
        for name in _REAL_TOOLS:
            real = shutil.which(name)
            assert real, f"{name} is needed to run start.sh"
            (self.tools / name).symlink_to(real)
        self._write_fake_curl()
        self._write_fake_cli_sh(cli_sh_exit)

    def _write_fake_curl(self) -> None:
        # Serves cli.sh from the fixture dir to the -o path and records its argv;
        # any other URL is an HTTP error (curl -f exits 22).
        curl = self.tools / "curl"
        curl.write_text(
            "#!/bin/sh\n"
            f'for a in "$@"; do printf "%s\\n" "$a"; done >> "{self.markers}/curl"\n'
            'out=""; url=""\n'
            "while [ $# -gt 0 ]; do\n"
            '  case "$1" in\n'
            '    -o) out="$2"; shift 2 ;;\n'
            '    *) url="$1"; shift ;;\n'
            "  esac\n"
            "done\n"
            'case "$url" in\n'
            f'  */cli.sh) cat "{self.fixtures}/cli.sh" > "$out" ;;\n'
            "  *) exit 22 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        curl.chmod(0o755)

    def _write_fake_cli_sh(self, exit_code: int) -> None:
        # Records its argv, then installs the kirocrew stub where cli.sh's managed
        # venv path links it (~/.local/bin), which is NOT on the harness PATH.
        (self.fixtures / "cli.sh").write_text(
            "#!/bin/sh\n"
            f'for a in "$@"; do printf "%s\\n" "$a"; done > "{self.markers}/cli-argv"\n'
            f"[ {exit_code} -eq 0 ] || exit {exit_code}\n"
            'mkdir -p "$HOME/.local/bin"\n'
            'cat > "$HOME/.local/bin/kirocrew" <<EOF\n'
            "#!/bin/sh\n"
            f'for a in "\\$@"; do printf "%s\\\\n" "\\$a"; done > "{self.markers}/kirocrew-argv"\n'
            "EOF\n"
            'chmod +x "$HOME/.local/bin/kirocrew"\n',
            encoding="utf-8",
        )

    def plant_kirocrew_on_path(self) -> None:
        stub = self.tools / "kirocrew"
        stub.write_text(
            "#!/bin/sh\n"
            f'for a in "$@"; do printf "%s\\n" "$a"; done > "{self.markers}/kirocrew-argv"\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)

    def env(self, **extra: str) -> dict[str, str]:
        return {
            "PATH": str(self.tools),
            "HOME": str(self.home),
            "KIROCREW_HOME": str(self.data_home),
            "TMPDIR": str(self.tmpdir),
            **extra,
        }

    def run(
        self, *args: str, script: Path = START_SH, **env: str
    ) -> subprocess.CompletedProcess[str]:
        return run_bounded(
            [str(self.tools / "sh"), str(script), *args], self.env(**env), cwd=str(self.root)
        )

    @property
    def curl_argv(self) -> list[str]:
        return _read_lines(self.markers / "curl")

    @property
    def cli_argv(self) -> list[str] | None:
        path = self.markers / "cli-argv"
        return _read_lines(path) if path.exists() else None

    @property
    def kirocrew_argv(self) -> list[str] | None:
        path = self.markers / "kirocrew-argv"
        return _read_lines(path) if path.exists() else None

    def leftover_downloads(self) -> list[str]:
        return sorted(p.name for p in self.tmpdir.iterdir() if p.name.startswith("kirocrew-cli."))


def test_start_sh_parses() -> None:
    """A syntax error here reaches every new user's shell."""
    subprocess.run(["sh", "-n", str(START_SH)], check=True)


def test_help_prints_usage_and_downloads_nothing(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    result = harness.run("--help")
    assert result.returncode == 0, result.stderr
    assert "curl -fsSL https://download.crew.kiro.dev/start.sh | sh" in result.stdout
    assert "--skip-install" in result.stdout
    assert harness.curl_argv == []


def test_unknown_argument_is_refused_before_any_download(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    result = harness.run("--definitely-not-a-flag")
    assert result.returncode == 2
    assert "unknown argument '--definitely-not-a-flag'" in result.stderr
    assert harness.curl_argv == []
    assert harness.kirocrew_argv is None


def test_cli_sh_gets_only_its_own_flags_and_kirocrew_start_gets_the_rest(
    tmp_path: Path,
) -> None:
    harness = _Harness(tmp_path)
    result = harness.run(
        "--channel",
        "insider",
        "--no-browser",
        "--version=0.6.0",
        "--foreground",
        "--system-python",
        "--cdn",
        "https://cdn.example.test/",
    )
    assert result.returncode == 0, result.stdout + result.stderr

    # cli.sh exits 2 on any flag it does not know, so start.sh's own must not reach it.
    assert harness.cli_argv == [
        "--channel",
        "insider",
        "--version=0.6.0",
        "--system-python",
        "--cdn",
        "https://cdn.example.test/",
    ]
    assert harness.kirocrew_argv == ["start", "--no-browser", "--foreground"]

    # --cdn moves where cli.sh itself comes from, with one trailing slash dropped,
    # and the download is pinned to HTTPS.
    curl = harness.curl_argv
    assert curl[-1] == "https://cdn.example.test/cli.sh"
    assert curl[curl.index("--proto") + 1] == "=https"
    assert "--tlsv1.2" in curl
    assert (harness.data_home / "install-origin").read_text(encoding="utf-8") == "start\n"
    assert harness.leftover_downloads() == []


def test_the_default_base_is_the_cdn_and_honours_the_env_override(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    assert harness.run().returncode == 0
    assert harness.curl_argv[-1] == "https://download.crew.kiro.dev/cli.sh"
    assert harness.cli_argv == []
    assert harness.kirocrew_argv == ["start"]

    other = _Harness(tmp_path / "second")
    result = other.run(KIROCREW_CDN_BASE="https://mirror.example.test")
    assert result.returncode == 0, result.stderr
    assert other.curl_argv[-1] == "https://mirror.example.test/cli.sh"


def test_a_value_flag_without_a_value_is_refused(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    result = harness.run("--channel")
    assert result.returncode != 0
    assert "--channel needs a value" in result.stderr
    assert harness.curl_argv == []


def test_skip_install_uses_the_kirocrew_already_on_path(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    harness.plant_kirocrew_on_path()
    result = harness.run("--skip-install", "--no-browser")
    assert result.returncode == 0, result.stderr
    assert harness.curl_argv == []
    assert harness.cli_argv is None
    assert harness.kirocrew_argv == ["start", "--no-browser"]
    # Nothing was installed here, so this run is not the install's origin.
    assert not (harness.data_home / "install-origin").exists()


def test_skip_install_still_installs_when_there_is_no_kirocrew(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    result = harness.run("--skip-install")
    assert result.returncode == 0, result.stderr
    assert harness.cli_argv == []
    assert harness.kirocrew_argv == ["start"]


def test_a_failed_install_starts_nothing_and_keeps_its_status(tmp_path: Path) -> None:
    harness = _Harness(tmp_path, cli_sh_exit=7)
    result = harness.run("--no-browser")
    assert result.returncode == 7
    assert "failed with exit status 7" in result.stderr
    assert harness.kirocrew_argv is None
    assert not (harness.data_home / "install-origin").exists()
    assert harness.leftover_downloads() == []


def test_the_marker_write_does_not_follow_a_planted_symlink(tmp_path: Path) -> None:
    harness = _Harness(tmp_path)
    victim = tmp_path / "victim"
    victim.write_text("untouched\n", encoding="utf-8")
    harness.data_home.mkdir()
    (harness.data_home / "install-origin").symlink_to(victim)
    assert harness.run().returncode == 0
    assert victim.read_text(encoding="utf-8") == "untouched\n"
    marker = harness.data_home / "install-origin"
    assert not marker.is_symlink()
    assert marker.read_text(encoding="utf-8") == "start\n"


@pytest.mark.parametrize(
    "cut",
    [
        pytest.param(lambda text: len(text) // 4, id="first-quarter"),
        pytest.param(lambda text: len(text) // 2, id="half"),
        pytest.param(lambda text: text.index("trap '_ks_cleanup' EXIT"), id="inside-main"),
        pytest.param(lambda text: text.rindex('main "$@"'), id="before-the-last-line"),
    ],
)
def test_a_truncated_download_runs_nothing(tmp_path: Path, cut) -> None:
    """Under ``curl | sh`` a dropped connection hands sh a prefix of the script.

    Every statement is inside ``main``, which only the last line calls, so any
    prefix either fails to parse or defines functions and stops.
    """
    text = START_SH.read_text(encoding="utf-8")
    truncated = tmp_path / "start-truncated.sh"
    truncated.write_text(text[: cut(text)], encoding="utf-8")
    harness = _Harness(tmp_path / "run")
    harness.run(script=truncated)
    assert harness.curl_argv == []
    assert harness.cli_argv is None
    assert harness.kirocrew_argv is None
    assert not harness.data_home.exists()
