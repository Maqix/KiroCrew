"""Regression guard for the baked ``GATEWAY-MODULES`` step (3b2) in
``packaging/build-desktop.sh``.

An edition whose CLI launcher runs its gateway as ``python -m <its module>``
names that module through ``KIROCREW_GATEWAY_MODULES``. The step writes the
names as a JSON array to ``website/electron/GATEWAY-MODULES``, which is packed
into ``app.asar`` and read by ``readEditionGatewayModules``. The reader ignores
a malformed file, so a bad value must fail the BUILD, and the staged file must
never outlive the run. The step is extracted from the shipped script so a
revert is what runs here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

runs_the_step = pytest.mark.skipif(
    os.name == "nt" or shutil.which("node") is None,
    reason="build-desktop.sh is a bash script and the step validates with node",
)

SCRIPT = Path(__file__).parent.parent / "packaging" / "build-desktop.sh"


def _extract_step() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"(# --- 3b2\. .*?)\n# --- (?!3b2\. )", text, re.DOTALL)
    assert m, "step 3b2 (baked GATEWAY-MODULES) not found in packaging/build-desktop.sh"
    return m.group(1)


def _run(tmp_path: Path, value: str | None) -> tuple[subprocess.CompletedProcess, Path]:
    electron_dir = tmp_path / "electron"
    electron_dir.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "KIROCREW_GATEWAY_MODULES"}
    if value is not None:
        env["KIROCREW_GATEWAY_MODULES"] = value
    env["ELECTRON_DIR"] = str(electron_dir)
    # The step's EXIT trap removes the staged file, so capture it into a
    # witness before the shell exits.
    script = (
        'set -euo pipefail\nlog() { echo "$@"; }\n'
        + _extract_step()
        + '\nif [ -f "$ELECTRON_DIR/GATEWAY-MODULES" ]; then cp "$ELECTRON_DIR/GATEWAY-MODULES" "$ELECTRON_DIR/.witness"; fi\n'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    return result, electron_dir


def test_the_step_cleans_both_baked_files_up_before_the_skip_electron_exit() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    cleanup = text.index('rm -f "$ELECTRON_DIR/EXTERNALLY-MANAGED" "$ELECTRON_DIR/GATEWAY-MODULES"')
    assert cleanup < text.index('if [ "${SKIP_ELECTRON:-0}" = "1" ]')


def test_the_trap_covers_the_marker_step_it_replaces() -> None:
    # A second EXIT trap replaces the first, so this step's trap must also remove
    # the EXTERNALLY-MANAGED marker step 3b may have staged.
    assert (
        'trap \'rm -f "$ELECTRON_DIR/EXTERNALLY-MANAGED" "$ELECTRON_DIR/GATEWAY-MODULES"\' EXIT'
        in _extract_step()
    )


@runs_the_step
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("acme_edition", ["acme_edition"]),
        ("acme_edition, acme.gateway", ["acme_edition", "acme.gateway"]),
        ("acme_edition acme_edition", ["acme_edition"]),
    ],
)
def test_valid_names_are_staged_as_a_json_array_and_removed_on_exit(
    tmp_path: Path, value: str, expected: list[str]
) -> None:
    result, electron_dir = _run(tmp_path, value)
    assert result.returncode == 0, result.stderr
    assert json.loads((electron_dir / ".witness").read_text(encoding="utf-8")) == expected
    assert not (electron_dir / "GATEWAY-MODULES").exists()


@runs_the_step
@pytest.mark.parametrize("value", ["../acme", "acme-edition", "-c", "acme;rm", ", ,", "1acme"])
def test_invalid_names_fail_the_build(tmp_path: Path, value: str) -> None:
    result, electron_dir = _run(tmp_path, value)
    assert result.returncode != 0
    assert "KIROCREW_GATEWAY_MODULES rejected" in result.stderr
    assert not (electron_dir / ".witness").exists()
    assert not (electron_dir / "GATEWAY-MODULES").exists()


@runs_the_step
def test_unset_stages_nothing(tmp_path: Path) -> None:
    result, electron_dir = _run(tmp_path, None)
    assert result.returncode == 0, result.stderr
    assert not (electron_dir / ".witness").exists()
