"""Append-only audit log of skill-library mutations.

Every create / edit / patch / delete / write_file / remove_file on a skill is
recorded with its WRITE ORIGIN — foreground (user-directed) vs the background
self-improvement review fork — plus a timestamp and success flag.

Why: skills are loadable procedural instructions. The background-review fork
replays raw conversation content (which may include tool-read documents, web
pages, etc.) through an agent that holds skill-write tools, so hostile content
can attempt to plant or mutate a skill that then loads in future sessions. This
log makes such a write traceable to WHEN and HOW it was introduced, and gives
an operator the record needed to revert it. It also captures BLOCKED attempts
(success=False) so injection attempts that the security scan or guards rejected
are still visible.

Best-effort: a logging failure must never break skill management. On a
network-mounted HERMES_HOME (gcsfuse) cross-process appends are not atomic;
entries are low-frequency so occasional loss is acceptable for an audit trail.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

_AUDIT_FILENAME = "skill_audit.jsonl"

_MUTATING_ACTIONS = frozenset(
    {"create", "edit", "patch", "delete", "write_file", "remove_file"}
)


def audit_log_path() -> Path:
    return get_hermes_home() / _AUDIT_FILENAME


def record_skill_mutation(
    action: str,
    name: str,
    *,
    origin: str,
    success: bool,
    detail: Optional[str] = None,
) -> None:
    """Append one audit record for a skill mutation. Never raises.

    ``origin`` is the write origin from tools.skill_provenance
    (``"foreground"`` or ``"background_review"``). ``detail`` is an optional
    short note (e.g. the error message for a blocked write); truncated.
    """
    if action not in _MUTATING_ACTIONS:
        return
    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "skill": name,
            "origin": origin,
            "success": bool(success),
        }
        if detail:
            entry["detail"] = str(detail)[:500]
        path = audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        logger.debug("skill audit log write failed", exc_info=True)
