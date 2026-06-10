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


# --- end-to-end: the skill_manage hook actually wires to the audit log --------
#
# The unit tests above call record_skill_mutation directly; these drive the real
# skill_manage() dispatch so a broken/removed hook is caught (it otherwise ships
# green). They also prove the ContextVar origin attribution — the crux of the
# defense: a background-review-fork write is recorded as "background_review".
from contextlib import contextmanager
from unittest.mock import patch

from tools.skill_manager_tool import skill_manage
from tools.skill_provenance import set_current_write_origin, reset_current_write_origin

_VALID_SKILL = (
    "---\nname: audit-probe\ndescription: A skill to test the audit hook.\n---\n\n"
    "# Audit Probe\n\nStep 1: exist.\n"
)


@contextmanager
def _wired(tmp_path, monkeypatch):
    monkeypatch.setattr(sal, "get_hermes_home", lambda: tmp_path)
    with patch("tools.skill_manager_tool.SKILLS_DIR", tmp_path), \
            patch("agent.skill_utils.get_all_skills_dirs", return_value=[tmp_path]):
        yield


def _audit_entries(tmp_path):
    p = tmp_path / "skill_audit.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_skill_manage_create_is_audited(tmp_path, monkeypatch):
    with _wired(tmp_path, monkeypatch):
        out = json.loads(skill_manage(action="create", name="audit-probe", content=_VALID_SKILL))
    assert out["success"] is True
    entries = _audit_entries(tmp_path)
    assert any(e["action"] == "create" and e["skill"] == "audit-probe" and e["success"] for e in entries)


def test_skill_manage_audits_background_review_origin(tmp_path, monkeypatch):
    token = set_current_write_origin("background_review")
    try:
        with _wired(tmp_path, monkeypatch):
            json.loads(skill_manage(action="create", name="audit-probe", content=_VALID_SKILL))
    finally:
        reset_current_write_origin(token)
    entries = _audit_entries(tmp_path)
    creates = [e for e in entries if e["action"] == "create"]
    assert creates and creates[-1]["origin"] == "background_review"


def test_skill_manage_audits_blocked_attempt(tmp_path, monkeypatch):
    # An invalid-name create is blocked by validation; the audit must still
    # record it (success=False) so blocked/injection attempts are visible.
    with _wired(tmp_path, monkeypatch):
        out = json.loads(skill_manage(action="create", name="Bad Name!!", content=_VALID_SKILL))
    assert out["success"] is False
    entries = _audit_entries(tmp_path)
    assert any(e["action"] == "create" and e["success"] is False for e in entries)
