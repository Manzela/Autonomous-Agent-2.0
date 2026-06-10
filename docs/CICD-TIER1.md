# Tier-1 CI/CD — Autonomous-Agent-2.0

This repo inherits the upstream hermes-agent CI (SHA-pinned actions, OSV scanning,
supply-chain audit, docker-lint, Dependabot, history/contributor checks) and adds a
first-party security + delivery layer for **this** repo and its GCP deployment.

## Added in this repo

| Layer | Workflow / file | Purpose |
|---|---|---|
| SAST | `.github/workflows/codeql.yml` | CodeQL (python + js/ts), PR + weekly, SARIF → Security tab |
| Supply chain | `.github/workflows/scorecard.yml` | OpenSSF Scorecard grade |
| CD | `.github/workflows/cd-image-deploy.yml` | Build → Trivy scan → SBOM → provenance attest → push AR (SHA tag) → staging → production (gated) |
| Ownership | `.github/CODEOWNERS` | Required code-owner review on critical paths |
| Governance | `scripts/repo-bootstrap.sh` | Branch protection, environments, security toggles via API |

The CI gate (lint/typecheck/test) is provided by the inherited `tests.yml` + `lint.yml`
(both already SHA-pinned and PR-triggered). No duplicate CI added.

## One-time setup (after the first push)

The GCP side is **already provisioned** (discovered during the audit): WIF pool
`autonomousagent-github` / provider `autonomousagent-actions`, deploy SA
`autonomousagent-github-ci@autonomous-agent-2026.iam.gserviceaccount.com` with
`artifactregistry.writer` + `compute.instanceAdmin.v1` + `compute.osAdminLogin` +
`iap.tunnelResourceAccessor`, and AR repo `autonomousagent-images`. So setup is just:

1. `bash scripts/repo-bootstrap.sh` — branch protection on `main`; `staging`/`production`
   environments (production = required reviewers + 5 min wait); secret-scanning +
   push-protection (needs GitHub Advanced Security on a private repo) + Dependabot;
   and it **sets all CD Actions variables to the real values** (project, zone, AR repo,
   VM names, WIF provider, deploy SA).
2. **One IAM binding** (the script prints the exact command; review + run): allow
   `repo:Manzela/Autonomous-Agent-2.0` to impersonate the deploy SA via the existing
   WIF pool (`roles/iam.workloadIdentityUser` on the principalSet for this repo).
   Confirm the provider's attribute-condition permits this repository.

## Deploy model (matches the production VM)

The VMs run a Docker-Compose stack that pins `IMAGE_TAG` and **refuses `:latest`**. CD
writes the new short-SHA tag to **`/opt/hermes/bootstrap/image_tag.env`** (the exact file
the `docker-compose-hermes.service` `EnvironmentFile` and the watchdog both read) and
`systemctl restart docker-compose-hermes.service` — whose `ExecStartPre` runs
`docker compose pull` then `up -d`. Over **IAP-tunnelled SSH**. Staging first (auto +
health gate); production only on a `workflow_dispatch` with `deploy_production=true`
through the **required-reviewer** `production` environment. Rollback = re-run with a
previous SHA tag.

## Inherited workflows to disable on this fork

These target upstream NousResearch infra and will misfire here — disable or delete
after import (they are otherwise harmless if repo-gated):
`upload_to_pypi.yml` (publishes to PyPI), `deploy-site.yml` (upstream docs site),
`skills-index.yml` / `skills-index-freshness.yml` (push to upstream index),
`docker-publish.yml` (already gated to `NousResearch/hermes-agent`, so it no-ops).
Disable with: `gh workflow disable <name> --repo Manzela/Autonomous-Agent-2.0`.
