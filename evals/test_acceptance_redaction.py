"""Acceptance eval — output secret-scrubbing (agent/redact.py).

This is a behavioral acceptance gate, not a unit test: it asserts the
product-level invariant the README promises ("output secret scrubbing — catches
stray credentials before persist or send"). Golden cases feed real-shaped
secrets through the scrubber and assert the raw secret never survives, plus
benign text is preserved. Deterministic, no model/network — runs as a CI gate.
"""
from __future__ import annotations

import pytest

from agent.redact import redact_sensitive_text


def _scrub(text: str) -> str:
    # force=True bypasses the HERMES_REDACT_SECRETS env gate so the eval is
    # deterministic regardless of environment.
    return redact_sensitive_text(text, force=True)


# (label, input containing a secret, the raw secret substring that MUST NOT survive)
GOLDEN_SECRETS = [
    ("auth-bearer", "Authorization: Bearer sk-live-9f8e7d6c5b4a3210ffeeddccbbaa9988", "sk-live-9f8e7d6c5b4a3210ffeeddccbbaa9988"),
    ("env-assign", "OPENAI_API_KEY=sk-proj-AbCdEf0123456789AbCdEf0123456789", "sk-proj-AbCdEf0123456789AbCdEf0123456789"),
    ("json-field", '{"api_key": "raw-secret-value-0123456789abcdef"}', "raw-secret-value-0123456789abcdef"),
    ("db-password", "DATABASE_URL=postgres://admin:Sup3rS3cretPw@db.internal:5432/app", "Sup3rS3cretPw"),
    ("telegram", "bot token 8123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx00", "8123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx00"),
    (
        "private-key",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIBVAIBADANBgkqhkiG9w0BAQEFAASCAT4wggE6\n-----END RSA PRIVATE KEY-----",
        "MIIBVAIBADANBgkqhkiG9w0BAQEFAASCAT4wggE6",
    ),
]


@pytest.mark.parametrize("label,text,secret", GOLDEN_SECRETS, ids=[g[0] for g in GOLDEN_SECRETS])
def test_secret_is_scrubbed(label: str, text: str, secret: str) -> None:
    out = _scrub(text)
    assert secret not in out, f"[{label}] raw secret survived redaction: {out!r}"
    # The output must change (something was redacted), not be silently passed through.
    assert out != text, f"[{label}] redactor made no change"


BENIGN = [
    "The quick brown fox jumps over the lazy dog.",
    "Deploying revision 12 to the staging environment now.",
    "See docs/CICD-TIER1.md for the pipeline overview.",
]


@pytest.mark.parametrize("text", BENIGN)
def test_benign_text_preserved(text: str) -> None:
    # Benign prose must survive intact — no over-redaction that would corrupt
    # normal agent output.
    assert _scrub(text) == text


def test_redaction_is_idempotent() -> None:
    # Scrubbing already-scrubbed text must not further mangle it (stable output).
    once = _scrub("OPENAI_API_KEY=sk-proj-AbCdEf0123456789AbCdEf0123456789")
    assert _scrub(once) == once
