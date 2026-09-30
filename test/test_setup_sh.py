"""Run setup's gateway-launch stage with a private HOME and fake host tools.

The build stages are excluded; the actual launch helper and final stage run
in bash/zsh. Neither the host's systemd manager nor its gateway is touched.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from installer_test_helpers import run_bounded

SETUP_SH = Path(__file__).resolve().parents[1] / "setup.sh"
pytestmark = pytest.mark.skipif(os.name == "nt", reason="setup.sh uses POSIX shells")


@pytest.fixture(params=["bash", "zsh"])
def shell(request) -> str:
    executable = shutil.which(request.param)
    if not executable:
        pytest.skip(f"{request.param} is not installed")
    return executable


class _Harness:
    def __init__(self, root: Path, shell: str, *, bus: str = "failed") -> None:
        self.root = root
        self.shell = shell
        self.tools = root / "tools"
        self.tools.mkdir()
        self.home = root / "home"
        self.home.mkdir()
        self.venv = root / "venv"
        (self.venv / "bin").mkdir(parents=True)
        self._script(
            self.venv / "bin" / "python",
            'printf "5476 %s\\n" "$INSTALLED_SERVICE"\n',
        )
        if bus != "missing":
            self._script(
                self.tools / "systemctl",
                'printf "%s\\n" "$@" > "$PROBE_LOG"\n'
                + (
                    f"exec {shlex.quote(sys.executable)} -c 'import time; time.sleep(30)'\n"
                    if bus == "hung"
                    else f"exit {0 if bus == 'healthy' else 1}\n"
                ),
            )

    @staticmethod
    def _script(path: Path, body: str) -> None:
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        path.chmod(0o755)

    def run(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        body = SETUP_SH.read_text(encoding="utf-8")
        helper = body.split("_kc_start_gateway() (", 1)[1].split("\n)\n", 1)[0]
        start = body[body.index('if [ "$_kc_start" = 1 ]; then\n    # ── 7. Start') :]
        # Source the real final stage, including cleanup and its returned status.
        stage = self.root / "stage.sh"
        stage.write_text(start, encoding="utf-8")
        driver = self.root / "driver.sh"
        driver.write_text(
            f"_py={shlex.quote(sys.executable)}\n"
            f"_venv={shlex.quote(str(self.venv))}\n"
            '_kc_start="$START_GATEWAY"\n'
            'uname() { if [ "$1" = -s ]; then echo "$HOST_OS"; else echo "$HOST_KERNEL"; fi; }\n'
            "kirocrew() {\n"
            '    printf "%s\\n" "$*" >> "$CALLS"\n'
            '    if [ "$1" = stop ]; then\n'
            '        printf "%s\\n" "${XDG_RUNTIME_DIR-<unset>}" "${DBUS_SESSION_BUS_ADDRESS-<unset>}" > "$STOP_ENV"\n'
            '        return "$STOP_EXIT"\n'
            "    fi\n"
            '    if [ "$1" = start ]; then\n'
            '        printf "%s\\n" "${XDG_RUNTIME_DIR-<unset>}" "${DBUS_SESSION_BUS_ADDRESS-<unset>}" > "$LAUNCH_ENV"\n'
            '        return "$START_EXIT"\n'
            "    fi\n"
            "}\n"
            "_kc_start_gateway() (" + helper + "\n)\n"
            f". {shlex.quote(str(stage))}\nresult=$?\n"
            'printf "%s\\n" "$XDG_RUNTIME_DIR" "$DBUS_SESSION_BUS_ADDRESS" > "$CALLER_ENV"\n'
            'exit "$result"\n',
            encoding="utf-8",
        )
        return run_bounded(
            [self.shell, str(driver)],
            {
                "HOME": str(self.home),
                "PATH": str(self.tools),
                "KIROCREW_HOME": str(self.home / "crew"),
                "HOST_OS": "Linux",
                "HOST_KERNEL": "6.6.87.2-microsoft-standard-WSL2",
                "XDG_RUNTIME_DIR": str(self.root / "runtime"),
                "DBUS_SESSION_BUS_ADDRESS": f"unix:path={self.root}/runtime/bus",
                "INSTALLED_SERVICE": "0",
                "START_GATEWAY": "1",
                "START_EXIT": "0",
                "STOP_EXIT": "0",
                "PROBE_LOG": str(self.root / "probe"),
                "CALLS": str(self.root / "calls"),
                "LAUNCH_ENV": str(self.root / "launch-env"),
                "STOP_ENV": str(self.root / "stop-env"),
                "CALLER_ENV": str(self.root / "caller-env"),
                **overrides,
            },
            cwd=str(self.root),
            timeout=15,
        )

    def lines(self, name: str) -> list[str]:
        path = self.root / name
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


@pytest.mark.parametrize("bus", ["failed", "missing", "hung"])
def test_wsl_unavailable_bus_continues_with_warning(tmp_path: Path, shell: str, bus: str):
    harness = _Harness(tmp_path, shell, bus=bus)
    result = harness.run()
    assert result.returncode == 0, result.stderr
    assert "WSL's systemd user bus is unavailable" in result.stdout
    assert "filesystem sandboxing; cgroup memory and process limits" in result.stdout
    assert "env -u XDG_RUNTIME_DIR -u DBUS_SESSION_BUS_ADDRESS kirocrew start" in result.stdout
    assert harness.lines("calls") == ["stop --port 5476", "start"]
    assert harness.lines("launch-env") == ["<unset>", "<unset>"]
    assert harness.lines("stop-env") == ["<unset>", "<unset>"]
    assert harness.lines("caller-env") == [
        str(tmp_path / "runtime"),
        f"unix:path={tmp_path}/runtime/bus",
    ]


def test_wsl_working_manager_preserves_limits_and_environment(tmp_path: Path, shell: str):
    harness = _Harness(tmp_path, shell, bus="healthy")
    result = harness.run()
    assert result.returncode == 0, result.stderr
    assert "user bus is unavailable" not in result.stdout
    assert harness.lines("probe") == ["--user", "show", "--property=Version", "--value"]
    assert harness.lines("launch-env") == harness.lines("caller-env")
    assert harness.lines("calls") == ["stop --port 5476", "start"]


@pytest.mark.parametrize("host,kernel", [("Linux", "6.8.0-generic"), ("Darwin", "24.0.0")])
def test_other_hosts_skip_the_wsl_probe(tmp_path: Path, shell: str, host: str, kernel: str):
    harness = _Harness(tmp_path, shell)
    result = harness.run(HOST_OS=host, HOST_KERNEL=kernel)
    assert result.returncode == 0, result.stderr
    assert harness.lines("probe") == []
    assert "user bus is unavailable" not in result.stdout
    assert harness.lines("launch-env") == harness.lines("caller-env")


def test_no_start_skips_the_probe_and_gateway(tmp_path: Path, shell: str):
    harness = _Harness(tmp_path, shell)
    result = harness.run(START_GATEWAY="0")
    assert result.returncode == 0, result.stderr
    assert harness.lines("probe") == []
    assert harness.lines("calls") == []


def test_installed_service_is_left_alone_and_warning_explains_limit(tmp_path: Path, shell: str):
    harness = _Harness(tmp_path, shell)
    result = harness.run(INSTALLED_SERVICE="1")
    assert result.returncode == 0, result.stderr
    assert harness.lines("calls") == ["start"]
    assert "An installed service keeps its own environment" in result.stdout


def test_gateway_failure_status_is_preserved(tmp_path: Path, shell: str):
    harness = _Harness(tmp_path, shell)
    assert harness.run(START_EXIT="7").returncode == 7


def test_no_running_gateway_does_not_block_start(tmp_path: Path, shell: str):
    harness = _Harness(tmp_path, shell)
    result = harness.run(STOP_EXIT="1")
    assert result.returncode == 0
    assert harness.lines("calls") == ["stop --port 5476", "start"]
