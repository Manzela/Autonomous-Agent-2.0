# evals/ — acceptance eval gate

Deterministic, behavioral **acceptance evals** for product-level safety/quality
invariants — not unit tests of implementation details. They run in CI via
`.github/workflows/eval-gate.yml` (PR + push) and locally with:

```
PYTHONPATH=. python -m pytest -q evals/
```

## Current evals

Each asserts a product-level safety control the README promises:

- **`test_acceptance_redaction.py`** — **output secret scrubbing** (`agent/redact.py`):
  real-shaped secrets (auth headers, env assignments, JSON secret fields, DB
  passwords, Telegram bot tokens, private-key blocks) are redacted before persist/send;
  benign text preserved; idempotent.
- **`test_acceptance_egress_ssrf.py`** — **network egress allowlist** (`tools/url_safety.py`):
  cloud metadata/credential endpoints (169.254.169.254, metadata.google.internal, ECS,
  Alibaba) are unconditionally blocked; private/loopback/link-local addresses and
  non-HTTP schemes (file/ftp/gopher) are refused — SSRF / credential-exfil protection.
- **`test_acceptance_memory_integrity.py`** — **memory/skill integrity** (P2-18,
  `tools/skill_audit_log.py`): background-review-fork skill writes are attributed as
  `background_review` (the injection-persistence vector is traceable), blocked attempts
  are still logged, read-only actions are not, and auditing never breaks a write.

## Adding evals

Each eval should assert an observable, deterministic invariant a user/operator
would care about (a safety control behaving, a tool routing correctly, a refusal
firing) without needing live model or network access. For model-quality
evaluation (golden datasets, LLM-as-judge), add a separate suite with pinned
fixtures and a judge-calibration guard so the gate stays deterministic.
