#!/usr/bin/env bash
# scripts/release.sh — automate the release bump (the 2x-proven process,
# REL-02/D-08 lineage): version lockstep, uv lock, CHANGELOG, build, tests,
# release commit + annotated tag.
#
# Usage:
#   scripts/release.sh [--dry-run] [--push] VERSION "release title"
#
#   scripts/release.sh 5.1.26 "every tail on the ONE strict parser"
#   scripts/release.sh --push 5.1.26 "..."     # also push branch + tag
#   scripts/release.sh --dry-run 5.1.26 "..."  # rails only, touch nothing
#
# What it does, in order (each step fails loudly — never a silent half-release):
#   1. Rails: on master, tracked worktree clean, VERSION is X.Y.Z, greater
#      than the current one, tag vX.Y.Z does not exist, CHANGELOG carries a
#      single `## [Unreleased]` section and no `## [VERSION]` yet.
#   2. Bump pyproject.toml `version`, pushframe/__init__.py `__version__`,
#      and the tests/test_version.py pin (exactly one line each).
#   3. `uv lock`, then assert the uv.lock diff is the pushframe bump alone.
#   4. CHANGELOG: `## [Unreleased]` -> `## [VERSION] - YYYY-MM-DD`.
#   5. `uv build` (wheel + sdist for VERSION must land in dist/).
#   6. `uv run --extra dev pytest -q -m "not live"`.
#   7. Commit the FIVE release files only — never `git add -A` (untracked
#      helper scripts must stay out of the release commit) — with the
#      `chore(release): vX.Y.Z — title` subject and the Codebuff trailer.
#   8. Annotated tag `pushframe vX.Y.Z`, body taken verbatim from the
#      released CHANGELOG section (### prefixes stripped to match tag style).
#   9. With --push: `git push origin master vX.Y.Z`. Always print the
#      post-push commands (CI watch, three-channel verification).
#
# The tag body is generated verbatim from the CHANGELOG; if you want the
# hand-condensed prose of previous tags, amend BEFORE pushing:
#   git tag -f -a vX.Y.Z -F -    # (only while the tag is unpushed)
set -euo pipefail

RELEASE_FILES=(pyproject.toml uv.lock pushframe/__init__.py tests/test_version.py CHANGELOG.md)

usage() {
  sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

die() { printf 'release: %s\n' "$*" >&2; exit 1; }

MODE=apply; PUSH=0; args=()
for a in "$@"; do
  case "$a" in
    --dry-run) MODE=dry ;;
    --push) PUSH=1 ;;
    -h|--help) usage 0 ;;
    *) args+=("$a") ;;
  esac
done
[ ${#args[@]} -eq 2 ] || { echo "release: expected VERSION and TITLE" >&2; usage 1; }
VERSION=${args[0]} TITLE=${args[1]}
[ -n "$TITLE" ] || die "the release title is empty"

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || die "not inside a git repository"
cd "$REPO_ROOT"
[ -f pyproject.toml ] || die "pyproject.toml not found — is this the pushframe repo?"
command -v uv >/dev/null || die "uv not found in PATH"

# --- 1. rails ---------------------------------------------------------------
branch=$(git symbolic-ref --short HEAD 2>/dev/null || true)
[ "$branch" = "master" ] || die "releases cut from master (you are on '${branch:-detached HEAD}')"
dirty=$(git status --porcelain --untracked-files=no)
[ -z "$dirty" ] || { echo "release: tracked worktree must be clean (a release commit is bump-only):" >&2; echo "$dirty" >&2; exit 1; }
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "VERSION must be X.Y.Z, got '$VERSION'"

cur=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -1)
[ -n "$cur" ] || die "could not read the current version from pyproject.toml"
[ "$cur" != "$VERSION" ] || die "pyproject is already at $VERSION"
[ "$(printf '%s\n%s\n' "$cur" "$VERSION" | sort -V | head -1)" = "$cur" ] \
  || die "$VERSION is not greater than the current $cur"
git rev-parse -q --verify "refs/tags/v$VERSION" >/dev/null \
  && die "tag v$VERSION already exists"
grep -q "^## \[$VERSION\]" CHANGELOG.md && die "CHANGELOG already has a [$VERSION] section"
n_unrel=$(grep -c '^## \[Unreleased\]' CHANGELOG.md || true)
[ "$n_unrel" = 1 ] || die "expected exactly one '## [Unreleased]' section in CHANGELOG.md, found $n_unrel (write the Fixed/Changed entries there first)"
echo "rails OK: master clean, $cur -> $VERSION, tag v$VERSION free, [Unreleased] present"

if [ "$MODE" = dry ]; then
  echo "--- dry run: no file, commit, or tag written ---"
  echo "would bump: pyproject.toml version / __init__ __version__ / test pin -> $VERSION"
  echo "would rename: '## [Unreleased]' -> '## [$VERSION] - $(date +%F)'"
  echo "would commit: git add ${RELEASE_FILES[*]}"
  echo "              chore(release): v$VERSION — $TITLE  (+ Codebuff trailer)"
  echo "would tag:    pushframe v$VERSION (annotated, body = CHANGELOG [$VERSION] section)"
  [ "$PUSH" = 1 ] && echo "would push:   git push origin master v$VERSION"
  exit 0
fi

# --- 2. version lockstep ----------------------------------------------------
sed -i "s/^version = \"[^\"]*\"$/version = \"$VERSION\"/" pyproject.toml
sed -i "s/^__version__ = \"[^\"]*\"$/__version__ = \"$VERSION\"/" pushframe/__init__.py
sed -i -E "s/^([[:space:]]*)assert __version__ == \"[^\"]*\"$/\1assert __version__ == \"$VERSION\"/" tests/test_version.py
[ "$(grep -c "^version = \"$VERSION\"$" pyproject.toml)" = 1 ] || die "pyproject.toml bump did not land exactly once"
[ "$(grep -c "^__version__ = \"$VERSION\"$" pushframe/__init__.py)" = 1 ] || die "__init__.py bump did not land exactly once"
[ "$(grep -c "assert __version__ == \"$VERSION\"" tests/test_version.py)" = 1 ] || die "test_version.py pin did not land exactly once"
echo "versions bumped to $VERSION (pyproject, __init__, test pin)"

# --- 3. uv lock, bump-only diff --------------------------------------------
uv lock
added=$(git diff -U0 -- uv.lock | { grep -E '^\+' || true; } | grep -vE '^\+\+\+ ' || true)
removed=$(git diff -U0 -- uv.lock | { grep -E '^-' || true; } | grep -vE '^--- ' || true)
[ "$added" = "+version = \"$VERSION\"" ] && [ "$removed" = "-version = \"$cur\"" ] \
  || die "uv.lock diff is not the pushframe version bump alone — inspect 'git diff uv.lock'"
echo "uv.lock diff is the pushframe bump alone"

# --- 4. CHANGELOG -----------------------------------------------------------
today=$(date +%F)
sed -i "0,/^## \[Unreleased\]/s//## [$VERSION] - $today/" CHANGELOG.md
grep -q "^## \[$VERSION\] - $today" CHANGELOG.md || die "CHANGELOG rename did not land"
echo "CHANGELOG: [Unreleased] -> [$VERSION] - $today"

# --- 5. build ---------------------------------------------------------------
uv build
ls dist/pushframe-"$VERSION"-py3-none-any.whl >/dev/null || die "wheel for $VERSION missing in dist/"
ls dist/pushframe-"$VERSION".tar.gz >/dev/null || die "sdist for $VERSION missing in dist/"
echo "built: dist/pushframe-$VERSION-py3-none-any.whl + dist/pushframe-$VERSION.tar.gz"

# --- 6. tests ---------------------------------------------------------------
echo "running the non-live suite…"
uv run --extra dev pytest -q -m "not live"

# --- 7. commit (explicit files only) ---------------------------------------
git add -- "${RELEASE_FILES[@]}"
git diff --cached --quiet && die "nothing staged — refusing an empty release commit"
git commit -F - <<MSG
chore(release): v$VERSION — $TITLE

🤖 Generated with Codebuff
Co-Authored-By: Codebuff <noreply@codebuff.com>
MSG
commit=$(git rev-parse --short HEAD)
echo "committed $commit: $(git log -1 --format=%s)"

# --- 8. annotated tag -------------------------------------------------------
start=$(grep -n "^## \[$VERSION\] - $today" CHANGELOG.md | head -1 | cut -d: -f1)
[ -n "$start" ] || die "cannot locate the [$VERSION] CHANGELOG section for the tag body"
body=$(awk -v s="$start" 'NR > s && /^## /{exit} NR > s {print}' CHANGELOG.md \
       | sed -e 's/^### //' -e 's/[[:space:]]*$//' -e '/./,$!d')
git tag -a "v$VERSION" -F - <<MSG
pushframe v$VERSION

$body
MSG
echo "tagged $(git describe --tags --exact-match) (annotated)"

# --- 9. push (opt-in) + next steps -----------------------------------------
if [ "$PUSH" = 1 ]; then
  git push origin master "v$VERSION"
  echo "pushed master + v$VERSION — watch CI:"
  echo "  gh run watch \$(gh run list --workflow=release --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status --interval 20"
else
  echo "next: git push origin master v$VERSION"
  echo "then: gh run watch \$(gh run list --workflow=release --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status --interval 20"
  echo "      gh run view <id> --json conclusion,jobs"
  echo "channels (after CI is green): PyPI json (allow 80-180 s of CDN lag),"
  echo "  gh release view v$VERSION --json tagName,isDraft,assets,"
  echo "  APT index coredmp95.github.io/pushframe/dists/stable/main/binary-amd64/Packages"
fi
