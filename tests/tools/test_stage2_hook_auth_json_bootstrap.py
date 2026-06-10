"""Contract test: the s6-overlay stage2 hook seeds auth.json from
HERMES_AUTH_JSON_BOOTSTRAP atomically and safely on first boot.

The previous implementation did a bare ``printf '%s' "$ENV" > auth.json``. A
write interrupted mid-stream (or an invalid env value) left a TRUNCATED/garbage
auth.json that the ``[ ! -f ]`` first-boot guard then protected forever,
permanently blocking re-seed of valid credentials. The corrected block:

  * writes to a temp file under ``umask 077`` (0600 at creation on filesystems
    that honour modes),
  * validates the temp file parses as JSON,
  * only then ``mv``-es it into place (atomic rename),
  * leaves auth.json UNSEEDED on invalid input, emitting a loud error.

These tests run the extracted block in a sandbox with ``chown``/``chmod``
stubbed (no real root) and a fake ``$INSTALL_DIR/.venv/bin/python`` that shells
out to the real interpreter for the JSON validation step.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGE2_HOOK = REPO_ROOT / "docker" / "stage2-hook.sh"


@pytest.fixture(scope="module")
def stage2_text() -> str:
    if not STAGE2_HOOK.exists():
        pytest.skip("docker/stage2-hook.sh not present in this checkout")
    return STAGE2_HOOK.read_text()


def _auth_block(text: str) -> str:
    """Extract the ``if [ ! -f "$HERMES_HOME/auth.json" ] … fi`` seed block.

    The outer ``fi`` is unindented (column 0); the inner ``fi`` of the
    JSON-validation if/else is indented, so anchoring on ``^fi$`` captures the
    whole block.
    """
    m = re.search(
        r'^(if \[ ! -f "\$HERMES_HOME/auth\.json" \].*?\nfi)$',
        text,
        re.DOTALL | re.MULTILINE,
    )
    assert m, (
        "stage2-hook.sh must contain the auth.json bootstrap-seed block guarded "
        "on HERMES_AUTH_JSON_BOOTSTRAP"
    )
    return m.group(1)


def test_auth_block_is_atomic_and_validated(stage2_text: str) -> None:
    block = _auth_block(stage2_text)
    # Atomic write: a temp file that is moved into place, not a bare redirect
    # straight onto auth.json.
    assert "mv -f" in block and "auth.json" in block
    assert "umask 077" in block, "temp file must be created under a restrictive umask"
    # JSON validation gate before the move.
    assert "json.load" in block
    # First-boot-only guard preserved.
    assert '[ ! -f "$HERMES_HOME/auth.json" ]' in block


def _run_auth_seed(
    text: str, *, env_value: str | None, chmod_fails: bool
) -> tuple[int, str | None, int | None, str]:
    """Run the extracted auth.json seed block in a sandbox.

    Returns (returncode, auth.json contents or None, file mode bits or None,
    stderr).
    """
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    block = _auth_block(text)

    with tempfile.TemporaryDirectory() as d:
        dpath = Path(d)
        home = dpath / "home"
        home.mkdir()
        # Fake $INSTALL_DIR/.venv/bin/python -> real interpreter, for the
        # JSON-validation step the block performs.
        venv_bin = dpath / "install" / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        py = venv_bin / "python"
        py.write_text(f'#!/bin/sh\nexec {shutil.which("python3") or sys.executable} "$@"\n')
        py.chmod(0o755)

        chmod_stub = "chmod() { return 1; }\n" if chmod_fails else "chmod() { command chmod \"$@\"; }\n"
        env_line = (
            f'export HERMES_AUTH_JSON_BOOTSTRAP={_sh_quote(env_value)}\n'
            if env_value is not None
            else "unset HERMES_AUTH_JSON_BOOTSTRAP\n"
        )
        script = (
            "set -eu\n"
            f'HERMES_HOME="{home}"\n'
            f'INSTALL_DIR="{dpath / "install"}"\n'
            "chown() { :; }\n"
            + chmod_stub
            + env_line
            + block
        )
        script_path = dpath / "harness.sh"
        script_path.write_text(script)

        proc = subprocess.run([bash, str(script_path)], capture_output=True, text=True)

        auth = home / "auth.json"
        contents = auth.read_text() if auth.exists() else None
        mode = stat.S_IMODE(auth.stat().st_mode) if auth.exists() else None
        # No stray temp files left behind.
        leftover = list(home.glob(".auth.json.bootstrap.*"))
        assert not leftover, f"temp bootstrap files left behind: {leftover}"
        return proc.returncode, contents, mode, proc.stderr


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def test_seeds_valid_json_at_0600(stage2_text: str) -> None:
    rc, contents, mode, _ = _run_auth_seed(
        stage2_text, env_value='{"token": "abc"}', chmod_fails=False
    )
    assert rc == 0
    assert contents is not None and json.loads(contents)["token"] == "abc"
    assert mode == 0o600, f"auth.json must be 0600, got {oct(mode or 0)}"


def test_chmod_failure_does_not_abort_or_lose_creds(stage2_text: str) -> None:
    # On gcsfuse chmod fails; the block must still seed auth.json (umask already
    # made it 0600) and must not abort the hook under `set -e`.
    rc, contents, _mode, _ = _run_auth_seed(
        stage2_text, env_value='{"token": "abc"}', chmod_fails=True
    )
    assert rc == 0, "chmod failure must be tolerated (|| true)"
    assert contents is not None and json.loads(contents)["token"] == "abc"


def test_invalid_json_leaves_auth_unseeded_and_warns(stage2_text: str) -> None:
    rc, contents, _mode, stderr = _run_auth_seed(
        stage2_text, env_value="not-json{", chmod_fails=False
    )
    assert rc == 0, "invalid input must not abort the hook"
    assert contents is None, "auth.json must NOT be created from invalid JSON"
    assert "not valid JSON" in stderr


def test_no_env_no_file(stage2_text: str) -> None:
    rc, contents, _mode, _ = _run_auth_seed(stage2_text, env_value=None, chmod_fails=False)
    assert rc == 0
    assert contents is None
