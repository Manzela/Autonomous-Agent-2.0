#!/usr/bin/env bash
# Configure Tier-1 governance on the standalone Hermes Agent repo via the GitHub
# API. Run AFTER the codebase is pushed (needs a `main` branch + workflows present
# so their checks can be required). Idempotent. Requires `gh` authed with admin.
set -euo pipefail

REPO="${REPO:-Manzela/Autonomous-Agent-2.0}"

echo "== 1. Repo hardening =="
gh api -X PATCH "repos/$REPO" \
  -F delete_branch_on_merge=true \
  -F allow_merge_commit=false -F allow_rebase_merge=false -F allow_squash_merge=true \
  -F allow_auto_merge=true -F has_wiki=false -F has_projects=false >/dev/null

echo "== 2. Security features (secret scanning + push protection, Dependabot) =="
gh api -X PATCH "repos/$REPO" -f 'security_and_analysis[secret_scanning][status]=enabled' \
  -f 'security_and_analysis[secret_scanning_push_protection][status]=enabled' \
  -f 'security_and_analysis[dependabot_security_updates][status]=enabled' >/dev/null || \
  echo "  (note: secret scanning on PRIVATE repos needs GitHub Advanced Security — enable in Settings if available)"
gh api -X PUT "repos/$REPO/vulnerability-alerts" >/dev/null 2>&1 || true
gh api -X PUT "repos/$REPO/automated-security-fixes" >/dev/null 2>&1 || true

echo "== 3. Branch protection on main =="
# Required checks use workflow JOB names. After the first CI run, confirm the exact
# names with: gh api repos/$REPO/commits/main/check-runs --jq '.check_runs[].name'
# and adjust the contexts below to match (a required check that never runs blocks merges).
# Required contexts are limited to the checks that run on EVERY PR to main with
# NO path filter (so a required check can never hang "expected" and wedge a merge):
#   - CodeQL python + js  (codeql.yml: no paths)   — SAST gate
#   - acceptance-evals    (eval-gate.yml: no paths) — safety-eval gate
# Tests / Lint / Image-scan run as visible checks but are path-filtered, so they
# are NOT hard-required here (requiring a path-skipped check blocks unrelated PRs).
# To promote tests to a hard gate cleanly, add an always-run aggregate "gate" job
# (needs: [test], if: always()) and require that single context instead.
#
# Reviews: required_approving_review_count is 0 on purpose. This is a solo-owned
# repo — requiring >=1 approval with enforce_admins=true would make it impossible
# for the owner to merge ANY PR (no second approver). PRs are still REQUIRED (no
# direct pushes to main) and the full CI gate is enforced for everyone (admins
# included). Bump to 1 + require_code_owner_reviews=true once collaborators exist.
gh api -X PUT "repos/$REPO/branches/main/protection" --input - >/dev/null <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "Analyze (python)",
      "Analyze (javascript-typescript)",
      "acceptance-evals"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0,
    "require_code_owner_reviews": false,
    "dismiss_stale_reviews": true
  },
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true,
  "restrictions": null
}
JSON

echo "Done."
echo "Next: review CI run, then tighten required contexts to the actual check-run names."
echo "      gh api repos/$REPO/commits/main/check-runs --jq '.check_runs[].name'"
