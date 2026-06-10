"""Tests for the append-only skill-mutation audit log (tools.skill_audit_log)."""
from __future__ import annotations

import json

import tools.skill_audit_log as sal


def test_appends_attributed_records(tmp_path, monkeypatch):
    monkeypatch.setattr(sal, "get_hermes_home", lambda: tmp_path)

    sal.record_skill_mutation("create", "my-skill", origin="background_review", success=True)
    sal.record_skill_mutation("delete", "bad", origin="foreground", success=False, detail="blocked by scan")

    lines = (tmp_path / "skill_audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    e0 = json.loads(lines[0])
    assert e0["action"] == "create"
    assert e0["skill"] == "my-skill"
    assert e0["origin"] == "background_review"  # the persistence-vector attribution
    assert e0["success"] is True
    assert "ts" in e0  # timestamped for traceability

    e1 = json.loads(lines[1])
    assert e1["success"] is False  # blocked attempts are recorded too
    assert e1["detail"] == "blocked by scan"


def test_ignores_non_mutating_actions(tmp_path, monkeypatch):
    monkeypatch.setattr(sal, "get_hermes_home", lambda: tmp_path)
    sal.record_skill_mutation("view", "x", origin="foreground", success=True)
    assert not (tmp_path / "skill_audit.jsonl").exists()


def test_never_raises_on_io_failure(monkeypatch):
    def _boom():
        raise OSError("home unavailable")

    monkeypatch.setattr(sal, "get_hermes_home", _boom)
    # Must not propagate — auditing is best-effort and cannot break skill writes.
    sal.record_skill_mutation("create", "x", origin="foreground", success=True)


def test_detail_is_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(sal, "get_hermes_home", lambda: tmp_path)
    sal.record_skill_mutation("patch", "x", origin="foreground", success=False, detail="z" * 2000)
    entry = json.loads((tmp_path / "skill_audit.jsonl").read_text(encoding="utf-8").strip())
    assert len(entry["detail"]) == 500
