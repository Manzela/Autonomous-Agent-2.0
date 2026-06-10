#!/usr/bin/env python3
"""Run Docker boot-time config migrations safely."""
from __future__ import annotations

import re
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

# Matches ONLY this tool's own backup shape: <name>.bak-<UTC stamp>[.<n>], where
# <n> is the collision suffix _backup_path appends within the same second.
_BACKUP_SUFFIX_RE = re.compile(r"\.bak-(\d{8}T\d{6}Z)(?:\.(\d+))?$")


def _prune_old_backups(
    path: Path, keep: int = _BACKUP_RETENTION, protect: Path | None = None
) -> None:
    """Keep only the newest ``keep`` timestamped backups for ``path``.

    Bounds the unbounded backup accumulation seen on network volumes where the
    migration re-runs every boot. Three correctness guarantees:
      * Only files matching THIS tool's exact ``.bak-<stamp>[.<n>]`` shape are
        candidates — operator/manual backups and ``.corrupt`` forensic artifacts
        are never deleted.
      * Ordering is by ``(stamp, int(collision_suffix))`` so ``.2`` sorts before
        ``.10`` (a raw name-sort inverts those). Within a single second the
        suffix is creation order, so a frozen clock still orders correctly.
      * ``protect`` (the just-created backup) is NEVER unlinked, so even a clock
        skewed backwards cannot delete the backup this run just wrote.
    Best-effort: unlink failures are ignored.
    """
    try:
        candidates: list[tuple[tuple[str, int], Path]] = []
        for p in path.parent.glob(f"{path.name}.bak-*"):
            m = _BACKUP_SUFFIX_RE.search(p.name)
            if m:
                candidates.append(((m.group(1), int(m.group(2) or 0)), p))
    except OSError:
        return
    candidates.sort(key=lambda t: t[0])
    ordered = [p for _, p in candidates]
    for stale in (ordered[:-keep] if keep > 0 else ordered):
        if protect is not None and stale == protect:
            continue
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
            # Tolerate so a backup failure never blocks the migration, but make
            # it operator-visible with the errno (ENOSPC/EACCES differ from the
            # expected gcsfuse path).
            print(
                f"[config-migrate] Warning: could not back up {path} "
                f"(errno {exc.errno}: {exc.strerror or exc}); continuing "
                f"migration without a backup for it",
                file=sys.stderr,
            )
            continue
        _prune_old_backups(path, protect=dest)
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
