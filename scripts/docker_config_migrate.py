#!/usr/bin/env python3
"""Run Docker boot-time config migrations safely."""
from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from hermes_cli.config import (
    check_config_version,
    get_config_path,
    get_env_path,
    migrate_config,
)
from utils import env_var_enabled


def _backup_path(path: Path, stamp: str) -> Path:
    base = path.with_name(f"{path.name}.bak-{stamp}")
    if not base.exists():
        return base
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.name}.bak-{stamp}.{index}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not choose a backup path for {path}")


_BACKUP_RETENTION = 5


def _prune_old_backups(path: Path, keep: int = _BACKUP_RETENTION) -> None:
    """Keep only the newest ``keep`` ``<name>.bak-*`` files for ``path``.

    Bounds the unbounded backup accumulation seen on network volumes where
    the migration re-runs every boot (each run creates a new timestamped
    backup). Best-effort: unlink failures are ignored.
    """
    try:
        backups = sorted(
            path.parent.glob(f"{path.name}.bak-*"),
            key=lambda p: p.name,
        )
    except OSError:
        return
    for stale in backups[:-keep] if keep > 0 else backups:
        try:
            stale.unlink()
        except OSError:
            pass


def _backup_existing(paths: Iterable[Path]) -> list[Path]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backups: list[Path] = []
    for path in paths:
        if not path.is_file():
            continue
        dest = _backup_path(path, stamp)
        try:
            # copyfile copies *data only* — no os.chmod/utime/copystat. On
            # network filesystems (gcsfuse, some NFS/SMB) chmod raises EPERM,
            # which previously aborted the entire migration every boot (the
            # backup succeeded, copymode failed) so the schema never advanced
            # and a new backup leaked on each restart. Data-only copy + a
            # tolerant guard keeps migrations running on those mounts.
            shutil.copyfile(path, dest)
            backups.append(dest)
        except OSError as exc:
            print(
                f"[config-migrate] Warning: could not back up {path} "
                f"({exc}); continuing migration without a backup for it"
            )
            continue
        _prune_old_backups(path)
    return backups


def main() -> int:
    if env_var_enabled("HERMES_SKIP_CONFIG_MIGRATION"):
        print("[config-migrate] HERMES_SKIP_CONFIG_MIGRATION is set; skipping config migration")
        return 0

    current_ver, latest_ver = check_config_version()
    if current_ver >= latest_ver:
        return 0

    backups = _backup_existing((get_config_path(), get_env_path()))
    backup_text = ", ".join(str(path) for path in backups) if backups else "none"
    print(
        f"[config-migrate] Migrating config schema {current_ver} -> {latest_ver}; "
        f"backups: {backup_text}"
    )
    migrate_config(interactive=False, quiet=False)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[config-migrate] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
