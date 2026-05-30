#!/usr/bin/env bash

# Deploy the generated static site to Codeberg Pages.
#
# This project keeps source files and generated website files separate:
#
#   - main:  project source, parser scripts, Word documents, README, schema, etc.
#   - pages: the static website served by Codeberg Pages.
#
# Codeberg Pages expects the publishable website files to be in the root of the
# `pages` branch. This script therefore:
#
#   1. Rebuilds public/resolutions.json from the Word documents.
#   2. Validates the generated JSON file.
#   3. Creates a temporary git worktree for the `pages` branch.
#   4. Copies public/ into that worktree root.
#   5. Commits the result if anything changed.
#   6. Pushes the `pages` branch to origin.
#
# The important final command is:
#
#   git push origin pages
#
# The script runs it from a worktree where the checked-out branch is `pages`.

set -euo pipefail

PAGES_BRANCH="${PAGES_BRANCH:-pages}"
REMOTE_NAME="${REMOTE_NAME:-origin}"
PUBLIC_DIR="${PUBLIC_DIR:-public}"
WORKTREE_DIR="${WORKTREE_DIR:-.worktree-codeberg-pages}"
COMMIT_MESSAGE="${COMMIT_MESSAGE:-Deploy static site to Codeberg Pages}"

log() {
  printf '[deploy] %s\n' "$*"
}

fail() {
  printf '[deploy] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

repo_root() {
  git rev-parse --show-toplevel 2>/dev/null
}

ensure_clean_worktree() {
  # Deploying from a dirty worktree is risky because generated output may not
  # correspond to a committed source state. Untracked files are allowed because
  # local editor files should not block deployment.
  if ! git diff --quiet || ! git diff --cached --quiet; then
    fail "Working tree has uncommitted changes. Commit or stash them before deploying."
  fi
}

ensure_pages_branch_exists() {
  if git show-ref --verify --quiet "refs/heads/${PAGES_BRANCH}"; then
    return
  fi

  log "Local ${PAGES_BRANCH} branch does not exist. Trying to fetch it from ${REMOTE_NAME}."
  git fetch "$REMOTE_NAME" "$PAGES_BRANCH":"$PAGES_BRANCH" 2>/dev/null || true

  if ! git show-ref --verify --quiet "refs/heads/${PAGES_BRANCH}"; then
    fail "Branch '${PAGES_BRANCH}' does not exist. Create it first and push it to Codeberg."
  fi
}

rebuild_public_data() {
  log "Rebuilding public/resolutions.json"
  python3 scripts/parse_docx.py source/*.docx --out "${PUBLIC_DIR}/resolutions.json"

  log "Validating generated data"
  python3 scripts/validate_data.py "${PUBLIC_DIR}/resolutions.json"
}

prepare_worktree() {
  if [ -e "$WORKTREE_DIR" ]; then
    fail "Worktree directory already exists: ${WORKTREE_DIR}. Remove it or set WORKTREE_DIR."
  fi

  log "Creating temporary worktree for branch ${PAGES_BRANCH}"
  git worktree add "$WORKTREE_DIR" "$PAGES_BRANCH"
}

cleanup_worktree() {
  if [ -d "$WORKTREE_DIR/.git" ] || [ -f "$WORKTREE_DIR/.git" ]; then
    log "Removing temporary worktree"
    git worktree remove "$WORKTREE_DIR" --force >/dev/null 2>&1 || true
  fi
}

copy_public_files() {
  log "Copying ${PUBLIC_DIR}/ to ${WORKTREE_DIR}/"

  # Keep the worktree root clean so files removed from public/ are also removed
  # from the published site. Preserve .git because it is the worktree metadata.
  find "$WORKTREE_DIR" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
  cp -a "${PUBLIC_DIR}/." "$WORKTREE_DIR/"
}

commit_and_push() {
  cd "$WORKTREE_DIR"

  git add -A

  if git diff --cached --quiet; then
    log "No changes to publish on ${PAGES_BRANCH}."
    return
  fi

  log "Committing changes to ${PAGES_BRANCH}"
  git commit -m "$COMMIT_MESSAGE"

  log "Pushing ${PAGES_BRANCH} to ${REMOTE_NAME}"
  git push "$REMOTE_NAME" "$PAGES_BRANCH"
}

main() {
  require_command git
  require_command python3

  local root
  root="$(repo_root)" || fail "This script must be run from inside a git repository."
  cd "$root"

  [ -d "$PUBLIC_DIR" ] || fail "Public directory not found: ${PUBLIC_DIR}"
  [ -d scripts ] || fail "scripts/ directory not found. Run from the project repository."

  ensure_clean_worktree
  ensure_pages_branch_exists

  trap cleanup_worktree EXIT

  rebuild_public_data
  prepare_worktree
  copy_public_files
  commit_and_push

  log "Done. Codeberg Pages should update shortly."
  log "Expected URL: https://madsdyd.codeberg.page/rv-resolutioner/"
}

main "$@"
