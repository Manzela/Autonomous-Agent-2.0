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

## Security posture (verified 2026-06)

- **Secret scan:** clean. A pre-push gitleaks scan found 768 hits, all verified false
  positives (public OAuth client IDs, the redaction regex, fixtures, docs). `.gitleaks.toml`
  allowlists them → `no leaks found`. **0 real secrets.**
- **SAST / change review:** the audit's own changes passed `/security-review` (no findings)
  and a 5-agent adversarial red-team.
- **Dependencies:** `uv.lock` carries 3 known upstream CVEs — aiohttp 3.13.3
  (only pinned in OPTIONAL extras: messaging/slack/sms; LOW/MODERATE), pygments 2.19.2
  (LOW, local ReDoS), pynacl 1.5.0 (MODERATE, local/high-complexity). **Pre-existing
  upstream pins, not introduced here.** *Detection:* Trivy scans the built image's
  installed deps in `image-build.yml`. *Remediation:* the repo's **Dependabot security
  updates** (CVE-only; enabled via repo settings) open pin-bump PRs when a pinned version
  becomes known-bad — which matches the repo's deliberate exact-pinning strategy
  (`.github/dependabot.yml`); scheduled version bumps are intentionally OFF. Moving these
  pins is a deliberate, reviewed, tested change — not an unverified manual bump of core
  networking/crypto deps inside this audit.

## Adding deployment later

When a deploy target exists, add a `cd.yml` that builds + pushes the image to your
registry and rolls the target. Use **Workload Identity Federation** (no SA keys) for
any cloud auth, gate production behind a GitHub **environment** with required
reviewers, and tag images by **immutable git SHA** (never `:latest`).

## Inherited upstream workflows

**Removed** (wrong target / would go red on a standalone fork): `upload_to_pypi.yml`,
`deploy-site.yml`, `skills-index.yml`, `skills-index-freshness.yml` (publish/deploy to
NousResearch infra); `nix.yml` + `nix-lockfile-fix.yml` (upstream Nix/cachix packaging,
needs a cachix cache + a GitHub App token); `osv-scanner.yml` (its SARIF upload needs
code scanning/GHAS — dependency vulns are covered by Trivy in `image-build.yml` + the
repo's Dependabot security updates instead). `docker-publish.yml` is repo-gated to
`NousResearch/hermes-agent`, so it no-ops here and is left in place. The remaining
check-only workflows (`contributor-check`, `history-check`, `docs-site-checks`) are
non-blocking (not in the required-status-checks set); adapt or disable them if their
upstream-specific assertions (AUTHOR_MAP, commit-history conventions) are unwanted:
`gh workflow disable <name> --repo Manzela/Autonomous-Agent-2.0`.
