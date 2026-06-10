# evals/ — acceptance eval gate

Deterministic, behavioral **acceptance evals** for product-level safety/quality
invariants — not unit tests of implementation details. They run in CI via
`.github/workflows/eval-gate.yml` (PR + push) and locally with:

```
PYTHONPATH=. python -m pytest -q evals/
```

## Current evals

- **`test_acceptance_redaction.py`** — the output secret-scrubber
  (`agent/redact.py`) must redact real-shaped secrets (auth headers, env
  assignments, JSON secret fields, DB connection passwords, Telegram bot tokens,
  private-key blocks) before they can be persisted or sent, while leaving benign
  text intact and being idempotent. This is the product's "output secret
  scrubbing" safety control.

## Adding evals

Each eval should assert an observable, deterministic invariant a user/operator
would care about (a safety control behaving, a tool routing correctly, a refusal
firing) without needing live model or network access. For model-quality
evaluation (golden datasets, LLM-as-judge), add a separate suite with pinned
fixtures and a judge-calibration guard so the gate stays deterministic.
