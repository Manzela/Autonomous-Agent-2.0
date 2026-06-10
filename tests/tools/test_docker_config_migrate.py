from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

from hermes_cli.config import DEFAULT_CONFIG

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "docker_config_migrate.py"


def _run_migration(hermes_home: Path, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "HERMES_HOME": str(hermes_home),
            "HERMES_SKIP_CHMOD": "1",
            "PYTHONPATH": str(REPO_ROOT),
        }
    )
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def test_docker_config_migrate_backs_up_and_migrates_legacy_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    model_map = {
        "local-small": {"context_length": 8192},
        "local-large": {"context_length": 32768},
    }
    config_path.write_text(
        yaml.safe_dump(
            {
                "_config_version": 11,
                "custom_providers": [
                    {
                        "name": "Local API",
                        "base_url": "http://localhost:8080/v1",
                        "api_key": "test-key",
                        "api_mode": "chat_completions",
                        "model": "local-small",
                        "models": model_map,
                        "context_length": 32768,
                        "discover_models": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    env_path.write_text("OPENROUTER_API_KEY=test\n", encoding="utf-8")

    proc = _run_migration(tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "Migrating config schema 11 ->" in proc.stdout
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["_config_version"] == DEFAULT_CONFIG["_config_version"]
    assert "custom_providers" not in raw
    provider = raw["providers"]["local-api"]
    assert provider["api"] == "http://localhost:8080/v1"
    assert provider["transport"] == "chat_completions"
    assert provider["default_model"] == "local-small"
    assert provider["models"] == model_map
    assert provider["context_length"] == 32768
    assert provider["discover_models"] is False
    assert list(tmp_path.glob("config.yaml.bak-*"))
    assert list(tmp_path.glob(".env.bak-*"))


def test_docker_config_migrate_backs_up_and_migrates_unversioned_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "custom_providers": [
                    {
                        "name": "Local API",
                        "base_url": "http://localhost:8080/v1",
                        "api_key": "test-key",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    proc = _run_migration(tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "Migrating config schema 0 ->" in proc.stdout
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["_config_version"] == DEFAULT_CONFIG["_config_version"]
    assert "custom_providers" not in raw
    assert raw["providers"]["local-api"]["api"] == "http://localhost:8080/v1"
    assert list(tmp_path.glob("config.yaml.bak-*"))


def test_docker_config_migrate_does_not_rewrite_invalid_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    original = "model: [unterminated\n"
    config_path.write_text(original, encoding="utf-8")

    proc = _run_migration(tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "Migrating config schema" not in proc.stdout
    assert "hermes config:" in proc.stderr
    assert config_path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("*.bak-*"))


def test_docker_config_migrate_skip_env_leaves_config_unchanged(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    original = yaml.safe_dump({"_config_version": 11})
    config_path.write_text(original, encoding="utf-8")

    proc = _run_migration(tmp_path, HERMES_SKIP_CONFIG_MIGRATION="1")

    assert proc.returncode == 0, proc.stderr
    assert "skipping config migration" in proc.stdout
    assert config_path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("*.bak-*"))


# --- gcsfuse contract: config-schema backup must not depend on chmod ---------
#
# On a network-mounted HERMES_HOME (gcsfuse, some NFS/SMB) os.chmod raises
# EPERM. The previous shutil.copy backup performed copymode() -> os.chmod and
# aborted the whole migration every boot (the backup succeeded, the chmod
# failed), so the config schema never advanced and a fresh .bak leaked on each
# restart. These tests pin the corrected contract: backups use copyfile (no
# metadata syscalls), a backup failure is tolerated, and accumulation is bounded.

import importlib.util
import shutil


def _load_migrate_module():
    spec = importlib.util.spec_from_file_location("_docker_config_migrate_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _raise_eperm(*_args, **_kwargs):
    raise OSError(1, "Operation not permitted")


def test_backup_does_not_depend_on_chmod(tmp_path: Path, monkeypatch) -> None:
    mod = _load_migrate_module()
    src = tmp_path / "config.yaml"
    src.write_text("model: gpt\n", encoding="utf-8")
    # A filesystem that rejects every mode change (gcsfuse-like).
    monkeypatch.setattr(os, "chmod", _raise_eperm)
    monkeypatch.setattr(shutil, "copymode", _raise_eperm)

    backups = mod._backup_existing([src])

    assert len(backups) == 1
    assert backups[0].exists()
    assert backups[0].read_text(encoding="utf-8") == "model: gpt\n"


def test_backup_failure_is_tolerated(tmp_path: Path, monkeypatch) -> None:
    mod = _load_migrate_module()
    src = tmp_path / "config.yaml"
    src.write_text("model: gpt\n", encoding="utf-8")
    # Even a hard copy failure (ENOSPC) must not raise out of _backup_existing —
    # migration should proceed backup-less rather than abort every boot.
    monkeypatch.setattr(mod.shutil, "copyfile", _raise_eperm)

    backups = mod._backup_existing([src])  # must not raise

    assert backups == []


def test_backup_retention_keeps_newest_five(tmp_path: Path) -> None:
    mod = _load_migrate_module()
    src = tmp_path / "config.yaml"
    src.write_text("model: gpt\n", encoding="utf-8")
    # Seven pre-existing timestamped backups (lexicographically sortable).
    for i in range(7):
        (tmp_path / f"config.yaml.bak-2026010{i}T000000Z").write_text("old", encoding="utf-8")

    mod._backup_existing([src])  # creates one more (now-stamped), prunes to 5

    remaining = sorted(p.name for p in tmp_path.glob("config.yaml.bak-*"))
    assert len(remaining) == mod._BACKUP_RETENTION == 5
    # The newest (just-created) backup is retained; the oldest are pruned.
    assert "config.yaml.bak-20260100T000000Z" not in remaining


def test_backup_retention_orders_collision_suffixes_numerically(tmp_path: Path) -> None:
    # Same-second backups disambiguate with .1/.2/.../.12. A lexicographic
    # name-sort would put .10/.11/.12 before .2 and prune the NEWEST; the parsed
    # (stamp, int) sort must keep the highest-suffix files.
    mod = _load_migrate_module()
    stamp = "20260101T000000Z"
    names = [f"config.yaml.bak-{stamp}"] + [f"config.yaml.bak-{stamp}.{i}" for i in range(1, 13)]
    for n in names:
        (tmp_path / n).write_text("x", encoding="utf-8")  # 13 backups, all same second

    mod._prune_old_backups(tmp_path / "config.yaml", keep=5)

    remaining = {p.name for p in tmp_path.glob("config.yaml.bak-*")}
    assert len(remaining) == 5
    # The five highest collision suffixes (.8..12) are the newest and survive;
    # the suffix-less base and low suffixes are pruned. (Set compare — a raw
    # name-sort would lexicographically order .10 before .2 and keep the wrong 5.)
    assert remaining == {f"config.yaml.bak-{stamp}.{i}" for i in (8, 9, 10, 11, 12)}


def test_backup_retention_never_prunes_just_created_or_foreign(tmp_path: Path) -> None:
    mod = _load_migrate_module()
    src = tmp_path / "config.yaml"
    src.write_text("model: gpt\n", encoding="utf-8")
    # Foreign artifacts that share the prefix but not the timestamp shape.
    (tmp_path / "config.yaml.bak-OLDmanual").write_text("manual", encoding="utf-8")
    (tmp_path / "config.yaml.bak-20260101T000000Z.corrupt").write_text("forensic", encoding="utf-8")
    # Plenty of real backups so the keep window is exceeded.
    for i in range(7):
        (tmp_path / f"config.yaml.bak-2026010{i}T000000Z").write_text("old", encoding="utf-8")

    created = mod._backup_existing([src])

    names = {p.name for p in tmp_path.glob("config.yaml.bak-*")}
    # Foreign / non-timestamp artifacts are never deleted.
    assert "config.yaml.bak-OLDmanual" in names
    assert "config.yaml.bak-20260101T000000Z.corrupt" in names
    # The just-created backup survives regardless of clock ordering.
    assert created and created[0].name in names
