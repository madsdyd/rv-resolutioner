#!/usr/bin/env bash

# Deploy the generated static site to GitHub Pages.
#
# This project keeps source files and generated website files separate:
#
#   - main:     project source, parser scripts, Word documents, README, schema, etc.
#   - gh-pages: the static website served by GitHub Pages.
#
# GitHub Pages can publish from the root of a selected branch. This script
# therefore:
#
#   1. Rebuilds public/resolutions.json from the Word documents.
#   2. Validates the generated JSON file.
#   3. Creates a temporary git worktree for the `gh-pages` branch.
#   4. Copies public/ into that worktree root.
#   5. Commits the result if anything changed.
#   6. Pushes the `gh-pages` branch to the GitHub remote.
#
# Defaults are chosen for this repository, but can be overridden:
#
#   REMOTE_NAME=github-origin PAGES_BRANCH=gh-pages ./scripts/deploy_github_pages.sh

set -euo pipefail

PAGES_BRANCH="${PAGES_BRANCH:-gh-pages}"
REMOTE_NAME="${REMOTE_NAME:-github-origin}"
PUBLIC_DIR="${PUBLIC_DIR:-public}"
WORKTREE_DIR="${WORKTREE_DIR:-.worktree-github-pages}"
COMMIT_MESSAGE="${COMMIT_MESSAGE:-Deploy static site to GitHub Pages}"
PAGES_URL="${PAGES_URL:-}"

log() {
  printf '[deploy-github] %s\n' "$*"
}

fail() {
  printf '[deploy-github] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

repo_root() {
  git rev-parse --show-toplevel 2>/dev/null
}


github_pages_url() {
  if [ -n "$PAGES_URL" ]; then
    printf '%s\n' "$PAGES_URL"
    return
  fi

  local remote_url owner repo path
  remote_url="$(git remote get-url "$REMOTE_NAME")"

  case "$remote_url" in
    https://github.com/*)
      path="${remote_url#https://github.com/}"
      ;;
    git@github.com:*)
      path="${remote_url#git@github.com:}"
      ;;
    ssh://git@github.com/*)
      path="${remote_url#ssh://git@github.com/}"
      ;;
    *)
      printf 'unknown; set PAGES_URL to print the published URL\n'
      return
      ;;
  esac

  path="${path%.git}"
  owner="${path%%/*}"
  repo="${path#*/}"

  if [ -z "$owner" ] || [ -z "$repo" ] || [ "$owner" = "$repo" ]; then
    printf 'unknown; set PAGES_URL to print the published URL\n'
    return
  fi

  if [ "$repo" = "${owner}.github.io" ]; then
    printf 'https://%s.github.io/\n' "$owner"
  else
    printf 'https://%s.github.io/%s/\n' "$owner" "$repo"
  fi
}

ensure_clean_worktree() {
  # Deploying from a dirty worktree is risky because generated output may not
  # correspond to a committed source state. Untracked files are allowed because
  # local editor files should not block deployment.
  if ! git diff --quiet || ! git diff --cached --quiet; then
    fail "Working tree has uncommitted changes. Commit or stash them before deploying."
  fi
}

ensure_remote_exists() {
  if ! git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
    fail "Remote '${REMOTE_NAME}' does not exist. Add it first or set REMOTE_NAME."
  fi
}

rebuild_public_data() {
  log "Rebuilding public/resolutions.json"
  uv run python scripts/parse_docx.py source/*.docx --out "${PUBLIC_DIR}/resolutions.json"

  log "Validating generated data"
  uv run python scripts/validate_data.py "${PUBLIC_DIR}/resolutions.json"
}

remove_stale_worktree() {
  # Clean up registrations for worktrees that were deleted manually.
  git worktree prune

  if [ -e "$WORKTREE_DIR" ]; then
    fail "Worktree directory already exists: ${WORKTREE_DIR}. Remove it or set WORKTREE_DIR."
  fi
}

prepare_worktree() {
  remove_stale_worktree

  if git show-ref --verify --quiet "refs/heads/${PAGES_BRANCH}"; then
    log "Creating temporary worktree for local branch ${PAGES_BRANCH}"
    git worktree add "$WORKTREE_DIR" "$PAGES_BRANCH"
    return
  fi

  log "Local ${PAGES_BRANCH} branch does not exist. Trying to fetch it from ${REMOTE_NAME}."
  git fetch "$REMOTE_NAME" "${PAGES_BRANCH}:${PAGES_BRANCH}" 2>/dev/null || true

  if git show-ref --verify --quiet "refs/heads/${PAGES_BRANCH}"; then
    log "Creating temporary worktree for fetched branch ${PAGES_BRANCH}"
    git worktree add "$WORKTREE_DIR" "$PAGES_BRANCH"
    return
  fi

  log "Branch ${PAGES_BRANCH} does not exist yet. Creating an orphan GitHub Pages branch."
  git worktree add --detach "$WORKTREE_DIR" HEAD
  git -C "$WORKTREE_DIR" switch --orphan "$PAGES_BRANCH"
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
  require_command uv

  local root
  root="$(repo_root)" || fail "This script must be run from inside a git repository."
  cd "$root"

  [ -d "$PUBLIC_DIR" ] || fail "Public directory not found: ${PUBLIC_DIR}"
  [ -d scripts ] || fail "scripts/ directory not found. Run from the project repository."

  ensure_clean_worktree
  ensure_remote_exists

  trap cleanup_worktree EXIT

  rebuild_public_data
  prepare_worktree
  copy_public_files
  commit_and_push

  log "Done. GitHub Pages should update shortly."
  log "GitHub Pages URL: $(github_pages_url)"
}

main "$@"
