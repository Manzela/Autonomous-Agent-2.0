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

1. `bash scripts/repo-bootstrap.sh` — branch protection on `main`, `staging`/`production`
   environments (production = required reviewers + 5 min wait), secret-scanning +
   push-protection (needs GitHub Advanced Security on a private repo), Dependabot.
2. **Workload Identity Federation** (no SA keys) so GitHub Actions can reach GCP:
   create a WIF pool+provider for `repo:Manzela/Autonomous-Agent-2.0`, a deploy SA with
   `roles/artifactregistry.writer` + `roles/compute.instanceAdmin.v1` +
   `roles/iam.serviceAccountTokenCreator`, bind the WIF principal to it.
3. Set the Actions **variables** (see the reminder printed by `repo-bootstrap.sh`):
   `GCP_PROJECT, GCP_ZONE, AR_REPO, STAGING_VM, PROD_VM, GCP_WIF_PROVIDER, GCP_DEPLOY_SA`.

## Deploy model (matches the production VM)

The VMs run a Docker-Compose stack that pins `IMAGE_TAG` and **refuses `:latest`**. CD
writes the new short-SHA tag to `/etc/hermes/image-tag.env` and restarts
`docker-compose-hermes.service` over **IAP-tunnelled SSH** — staging first (auto +
health gate), production only on a `workflow_dispatch` with `deploy_production=true`
through the **required-reviewer** `production` environment. Rollback = re-run with a
previous SHA tag.

> The exact VM roll step (`/etc/hermes/image-tag.env` + `systemctl restart`) assumes the
> compose `IMAGE_TAG` is sourced from that env file. If your bootstrap sources it
> differently, adjust the `--command` block in `cd-image-deploy.yml` accordingly.

## Inherited workflows to disable on this fork

These target upstream NousResearch infra and will misfire here — disable or delete
after import (they are otherwise harmless if repo-gated):
`upload_to_pypi.yml` (publishes to PyPI), `deploy-site.yml` (upstream docs site),
`skills-index.yml` / `skills-index-freshness.yml` (push to upstream index),
`docker-publish.yml` (already gated to `NousResearch/hermes-agent`, so it no-ops).
Disable with: `gh workflow disable <name> --repo Manzela/Autonomous-Agent-2.0`.
