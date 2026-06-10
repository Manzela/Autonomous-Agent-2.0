#!/usr/bin/env bash
# Configure Tier-1 governance on Manzela/Autonomous-Agent-2.0 via the GitHub API.
# Run AFTER the codebase is pushed (needs a `main` branch + the workflows present
# so their checks can be required). Idempotent. Requires `gh` authed with repo+admin.
set -euo pipefail

REPO="${REPO:-Manzela/Autonomous-Agent-2.0}"
OWNER="${REPO%/*}"; NAME="${REPO#*/}"

echo "== 1. Repo hardening =="
gh api -X PATCH "repos/$REPO" \
  -f delete_branch_on_merge=true \
  -F allow_merge_commit=false -F allow_rebase_merge=false -F allow_squash_merge=true \
  -F allow_auto_merge=true -F has_wiki=false -F has_projects=false >/dev/null

echo "== 2. Security features (secret scanning + push protection, Dependabot) =="
gh api -X PATCH "repos/$REPO" -f 'security_and_analysis[secret_scanning][status]=enabled' \
  -f 'security_and_analysis[secret_scanning_push_protection][status]=enabled' \
  -f 'security_and_analysis[dependabot_security_updates][status]=enabled' >/dev/null || \
  echo "  (note: secret scanning on PRIVATE repos needs GitHub Advanced Security — enable in Settings if available)"
gh api -X PUT "repos/$REPO/vulnerability-alerts" >/dev/null 2>&1 || true
gh api -X PUT "repos/$REPO/automated-security-fixes" >/dev/null 2>&1 || true

echo "== 3. Environments: staging (auto) + production (required reviewers + 5m wait) =="
gh api -X PUT "repos/$REPO/environments/staging" >/dev/null
REVIEWER_ID="$(gh api users/"$OWNER" --jq .id)"
gh api -X PUT "repos/$REPO/environments/production" \
  -F wait_timer=5 \
  -f 'deployment_branch_policy[protected_branches]=true' \
  -f 'deployment_branch_policy[custom_branch_policies]=false' \
  -F "reviewers[][type]=User" -F "reviewers[][id]=${REVIEWER_ID}" >/dev/null

echo "== 4. Branch protection on main =="
# Required checks use the workflow JOB names. Adjust if you rename jobs.
gh api -X PUT "repos/$REPO/branches/main/protection" --input - >/dev/null <<JSON
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "Analyze (python)",
      "Analyze (javascript-typescript)",
      "Build, scan, attest, push"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "require_code_owner_reviews": true,
    "dismiss_stale_reviews": true,
    "require_last_push_approval": true
  },
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true,
  "restrictions": null
}
JSON

echo "== 5. Set Actions repo VARIABLES for the CD pipeline (real discovered values) =="
gh variable set GCP_PROJECT      --repo "$REPO" --body "autonomous-agent-2026"
gh variable set GCP_ZONE         --repo "$REPO" --body "us-central1-a"
gh variable set AR_REPO          --repo "$REPO" --body "us-central1-docker.pkg.dev/autonomous-agent-2026/autonomousagent-images"
gh variable set STAGING_VM       --repo "$REPO" --body "autonomousagent-staging-vm"
gh variable set PROD_VM          --repo "$REPO" --body "autonomousagent-vm"
gh variable set GCP_WIF_PROVIDER --repo "$REPO" --body "projects/870615250682/locations/global/workloadIdentityPools/autonomousagent-github/providers/autonomousagent-actions"
gh variable set GCP_DEPLOY_SA    --repo "$REPO" --body "autonomousagent-github-ci@autonomous-agent-2026.iam.gserviceaccount.com"

echo "== 6. ONE-TIME IAM (run manually; review first) — let THIS repo's Actions impersonate the deploy SA via WIF =="
cat <<'IAM'
  # Bind the new repo's WIF principal to the existing deploy SA. Verify the WIF
  # provider's attribute-condition allows attribute.repository before running.
  gcloud iam service-accounts add-iam-policy-binding \
    autonomousagent-github-ci@autonomous-agent-2026.iam.gserviceaccount.com \
    --project autonomous-agent-2026 \
    --role roles/iam.workloadIdentityUser \
    --member 'principalSet://iam.googleapis.com/projects/870615250682/locations/global/workloadIdentityPools/autonomousagent-github/attribute.repository/Manzela/Autonomous-Agent-2.0'
IAM
echo "Done. Verify: gh api repos/$REPO/branches/main/protection --jq '.required_status_checks.contexts'"
