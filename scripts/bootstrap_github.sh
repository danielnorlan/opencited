#!/usr/bin/env bash
# Set up the repository on GitHub: the issue labels and milestones, squash merges
# with the pull request title as the whole commit message, and GitHub Actions limited
# to the actions the workflows use.
# Usage: scripts/bootstrap_github.sh [owner/repo]
# Needs the GitHub CLI, logged in with admin access to the repository. Safe to run again.
set -euo pipefail

repo="${1:-danielnorlan/opencited}"
description="Answers from authoritative public sources, with a citation for every number."

label() {
  gh label create "$1" --repo "$repo" --color "$2" --description "$3" --force
}

milestone() {
  local existing
  existing="$(gh api "repos/$repo/milestones?state=all&per_page=100" --jq '.[].title')"
  if grep -qxF -- "$1" <<<"$existing"; then
    echo "Milestone exists: $1"
  else
    gh api "repos/$repo/milestones" -f title="$1" -f description="$2" --silent
    echo "Created milestone: $1"
  fi
}

label "type: feature" "1d76db" "New behaviour"
label "type: bug" "d73a4a" "Something does not work as specified"
label "type: chore" "cfd3d7" "Maintenance, tooling or dependencies"
label "type: docs" "0075ca" "Documentation only"
label "area: sources" "5319e7" "Source clients and the MCP server"
label "area: agent" "c5def5" "Agent, calculation engine and evidence gate"
label "area: evals" "f9d0c4" "Evaluation suite and results"
label "area: tooling" "bfd4f2" "Development scripts, hooks and CI"

# GitHub creates these labels in every new repository. The labels above replace them.
for name in bug documentation duplicate enhancement "good first issue" "help wanted" \
  invalid question wontfix accessibility; do
  if gh label delete "$name" --repo "$repo" --yes >/dev/null 2>&1; then
    echo "Deleted default label: $name"
  fi
done

milestone "M1 MCP server" "SSB source and MCP server with provenance (spec 0001)."
milestone "M2 Evidence gate" "Calculation engine, evidence gate, agent and the first evaluation set."
milestone "M3 Search and tracing" "Hybrid table search, tracing and model routing."
milestone "M4 Public demo" "Demo site and a public results page."

gh repo edit "$repo" \
  --description "$description" \
  --enable-wiki=false \
  --enable-projects=false \
  --enable-discussions=false \
  --enable-squash-merge \
  --enable-merge-commit=false \
  --enable-rebase-merge=false \
  --delete-branch-on-merge
echo "Merging: squash only, branches deleted after merge. Wiki, projects and discussions off."

# The commit on main gets the title and number, and the description stays in the pull
# request. The update branch button would add merge commits made on GitHub to a pull
# request.
gh api --method PATCH "repos/$repo" \
  -f squash_merge_commit_title=PR_TITLE \
  -f squash_merge_commit_message=BLANK \
  -F allow_update_branch=false \
  --silent
echo "Squash commits: the pull request title, with no body."

# Workflows may use only the actions below, pinned to a full commit SHA. The token is
# read-only by default and cannot approve pull requests. Add an action here before a
# workflow uses it.
gh api --method PUT "repos/$repo/actions/permissions" \
  -F enabled=true -f allowed_actions=selected -F sha_pinning_required=true --silent
gh api --method PUT "repos/$repo/actions/permissions/selected-actions" \
  -F github_owned_allowed=false -F verified_allowed=false \
  -f 'patterns_allowed[]=actions/checkout@*' \
  -f 'patterns_allowed[]=actions/cache@*' \
  -f 'patterns_allowed[]=astral-sh/setup-uv@*' \
  --silent
gh api --method PUT "repos/$repo/actions/permissions/workflow" \
  -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false --silent
echo "Actions: checkout, cache and setup-uv only, pinned to a commit, with a read-only token."
