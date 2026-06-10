"""Acceptance eval — memory/skill mutation integrity (tools/skill_audit_log.py).

Asserts the audit's P2-18 defense: every skill mutation is recorded with its
WRITE ORIGIN, so a skill planted by the background self-improvement fork (the
prompt-injection persistence vector) is traceable, and blocked attempts are
captured too. Behavioral, deterministic, no model/network.
"""
from __future__ import annotations

import json

import tools.skill_audit_log as sal


def test_background_review_origin_is_recorded(tmp_path, monkeypatch) -> None:
    # A skill write attributed to the background-review fork must be logged AS
    # background_review — the traceability the defense depends on.
    monkeypatch.setattr(sal, "get_hermes_home", lambda: tmp_path)
    sal.record_skill_mutation("create", "planted-skill", origin="background_review", success=True)

    entries = [json.loads(l) for l in (tmp_path / "skill_audit.jsonl").read_text().splitlines() if l.strip()]
    assert any(
        e["action"] == "create" and e["skill"] == "planted-skill" and e["origin"] == "background_review"
        for e in entries
    ), "background-review skill write was not attributed in the audit log"


def test_blocked_attempt_is_recorded(tmp_path, monkeypatch) -> None:
    # A rejected mutation (success=False) must still be recorded, so injection
    # attempts the guards blocked remain visible to an operator.
    monkeypatch.setattr(sal, "get_hermes_home", lambda: tmp_path)
    sal.record_skill_mutation("delete", "victim", origin="background_review", success=False, detail="blocked by guard")

    entry = json.loads((tmp_path / "skill_audit.jsonl").read_text().strip())
    assert entry["success"] is False
    assert entry["detail"] == "blocked by guard"


def test_non_mutating_actions_not_logged(tmp_path, monkeypatch) -> None:
    # Read-only actions must not bloat the audit trail (only mutations matter).
    monkeypatch.setattr(sal, "get_hermes_home", lambda: tmp_path)
    sal.record_skill_mutation("view", "x", origin="foreground", success=True)
    assert not (tmp_path / "skill_audit.jsonl").exists()


def test_audit_logging_never_raises(monkeypatch) -> None:
    # Auditing is best-effort: it must never break a skill write, even if the
    # home dir is unavailable.
    def _boom():
        raise OSError("home unavailable")

    monkeypatch.setattr(sal, "get_hermes_home", _boom)
    sal.record_skill_mutation("create", "x", origin="foreground", success=True)  # must not raise
