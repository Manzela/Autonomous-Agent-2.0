# Tier-1 CI/CD — Autonomous-Agent-2.0 (standalone Hermes Agent)

This repo is a self-contained Hermes Agent deployment. It inherits the upstream
hermes-agent CI (SHA-pinned actions, OSV scanning, supply-chain audit, docker-lint,
Dependabot, history/contributor checks, lint/test) and adds a first-party security
layer. There is **no external deploy target wired in** — add one when ready.

## Added in this repo

| Layer | Workflow / file | Purpose |
|---|---|---|
| SAST | `.github/workflows/codeql.yml` | CodeQL (python + js/ts), PR + weekly, SARIF → Security tab |
| Supply chain | `.github/workflows/scorecard.yml` | OpenSSF Scorecard grade |
| Image | `.github/workflows/image-build.yml` | Build the Docker image, Trivy HIGH/CRITICAL scan, SBOM artifact — no push, no deploy |
| Ownership | `.github/CODEOWNERS` | Required code-owner review on critical paths |
| Governance | `scripts/repo-bootstrap.sh` | Branch protection, security toggles, repo hardening via the API |

CI lint/typecheck/test is provided by the inherited `tests.yml` + `lint.yml`
(SHA-pinned, PR-triggered). No duplicate CI added.

## One-time setup (after the first push)

1. `bash scripts/repo-bootstrap.sh` — squash-only + delete-branch-on-merge; secret
   scanning + push protection (needs GitHub Advanced Security on a private repo) +
   Dependabot; branch protection on `main` (required PR review + CODEOWNERS, linear
   history, no force-push, conversation resolution, required status checks).
2. After the first CI run, align the required-check **contexts** to the actual
   check-run names:
   `gh api repos/Manzela/Autonomous-Agent-2.0/commits/main/check-runs --jq '.check_runs[].name'`
   then re-run `repo-bootstrap.sh` (or PATCH the protection) with those names.

## Adding deployment later

When a deploy target exists, add a `cd.yml` that builds + pushes the image to your
registry and rolls the target. Use **Workload Identity Federation** (no SA keys) for
any cloud auth, gate production behind a GitHub **environment** with required
reviewers, and tag images by **immutable git SHA** (never `:latest`).

## Inherited workflows to disable on this fork

These target upstream NousResearch infra and will misfire here — disable after import:
`upload_to_pypi.yml`, `deploy-site.yml`, `skills-index.yml`, `skills-index-freshness.yml`
(`docker-publish.yml` is already repo-gated to NousResearch, so it no-ops). Disable with
`gh workflow disable <name> --repo Manzela/Autonomous-Agent-2.0`.
