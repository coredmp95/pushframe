#!/usr/bin/env bash
# Assemble the signed APT repository and publish it to GitHub Pages
# (phase 21, DEB-05; decisions D-04/D-05/D-10/D-11/D-12).
#
# Zero-dependency assembly (D-10): dpkg-scanpackages + a hand-built Release,
# no apt-ftparchive/reprepro. The tree:
#
#   pool/main/p/pushframe/pushframe_<v>_amd64.deb
#   dists/stable/main/binary-amd64/Packages
#   dists/stable/Release            (hand-built, SHA256 sums)
#   dists/stable/InRelease          (clearsigned)
#   dists/stable/Release.gpg        (detached)
#   dists/pushframe.asc             (public key for the keyring journey)
#
# Publishing (D-12): orphan-commit the tree to the gh-pages branch of origin
# and force-push — Pages (source gh-pages, root) then serves it at
# https://coredmp95.github.io/pushframe/ (already enabled, HTTPS-enforced).
#
# --dry-run: assemble + sign the tree, print it and the exact git commands
# that WOULD run; publish nothing.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

DRY_RUN=false
[ "${1:-}" = "--dry-run" ] && DRY_RUN=true

# --- inputs -------------------------------------------------------------------
if ! ls dist/pushframe_*_amd64.deb >/dev/null 2>&1; then
    echo "== no deb, building =="
    ./scripts/build-deb.sh >/dev/null
fi
DEB=$(ls dist/pushframe_*_amd64.deb | head -1)

./scripts/make-apt-key.sh >/dev/null
FP=$(cat "${PUSHFRAME_APT_KEY_DIR:-$HOME/.config/pushframe-apt-key}/fingerprint")
echo "== signing key: $FP =="

# --- assemble -----------------------------------------------------------------
TREE="build/apt-repo"
rm -rf "$TREE"
mkdir -p "$TREE/pool/main/p/pushframe" "$TREE/dists/stable/main/binary-amd64"

cp "$DEB" "$TREE/pool/main/p/pushframe/"
cp "$DEB" build/apt-repo.deb   # flat copy: the journey test consumes one path

DEB_BASE=$(basename "$DEB")
POOL_REL="pool/main/p/pushframe/$DEB_BASE"

echo "== scanning packages =="
(cd "$TREE" && dpkg-scanpackages --multiversion pool/ /dev/null \
    > dists/stable/main/binary-amd64/Packages 2>/dev/null)
gzip -9n -c "$TREE/dists/stable/main/binary-amd64/Packages" \
    > "$TREE/dists/stable/main/binary-amd64/Packages.gz"

# --- hand-built Release (D-10) -------------------------------------------------
# Checksum paths are RELATIVE TO dists/stable/ and carry the REAL byte size;
# pool debs are NOT listed here — their integrity comes from the Packages
# hashes (Debian convention). First attempt mis-parsed sha256sum fields
# (sizes 0, paths empty) and apt correctly rejected the repo as
# "weak security information" — journey-test catch, 2026-09-29.
# apt's Release Date parser wants an RFC1123 zone NAME (UTC), not a numeric
# offset — "Date: … +0200" yields "W: Invalid 'Date' entry" (journey-test
# catch 2026-09-29); real Debian repos use the UTC zone name.
DATE=$(LC_ALL=C date -u '+%a, %d %b %Y %H:%M:%S UTC')
(
  cd "$TREE/dists/stable"
  {
    cat <<RELEASEHDR
Origin: pushframe
Label: pushframe
Suite: stable
Codename: stable
Architectures: amd64
Components: main
Description: unofficial pushframe CLI repository (not affiliated with Aura Frames Inc.)
Date: $DATE
SHA256:
RELEASEHDR
    for f in main/binary-amd64/Packages main/binary-amd64/Packages.gz; do
      printf ' %s %s %s\n' "$(sha256sum "$f" | cut -d' ' -f1)" "$(stat -c%s "$f")" "$f"
    done
  } > Release
)

# --- sign (D-04: dedicated no-passphrase key) ----------------------------------
echo "== signing =="
gpg --batch --yes --clearsign --local-user "$FP" \
    -o "$TREE/dists/stable/InRelease" "$TREE/dists/stable/Release"
gpg --batch --yes --armor --detach-sign --local-user "$FP" \
    -o "$TREE/dists/stable/Release.gpg" "$TREE/dists/stable/Release"
gpg --armor --export "$FP" > "$TREE/dists/pushframe.asc"

# human landing page (gh-pages root): the site must explain itself to a
# visitor, not serve a bare 404 around the apt paths.
cat > "$TREE/index.html" <<HTMLPAGE
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>pushframe APT repository</title></head>
<body>
<h1>pushframe — APT repository</h1>
<p>Signed Debian repository for the <strong>pushframe</strong> CLI
(unofficial community tool for Aura Frames, not affiliated with Aura Frames Inc.).</p>
<p>Signing key fingerprint: <code>$FP</code></p>
<p>Install instructions: see the
<a href="https://github.com/coredmp95/pushframe#install-ubuntudebian">README</a>.</p>
</body>
</html>
HTMLPAGE

# --- verify with the exported key in an ISOLATED keyring -----------------------
GNUPGHOME=$(mktemp -d); trap 'rm -rf "$GNUPGHOME"' EXIT
gpg --homedir "$GNUPGHOME" --import "$TREE/dists/pushframe.asc" >/dev/null 2>&1
gpg --homedir "$GNUPGHOME" --verify "$TREE/dists/stable/InRelease" >/dev/null 2>&1
echo "== signature verified with exported key only: OK =="

# --- output / publish ----------------------------------------------------------
echo "== tree (build/apt-repo) =="
find "$TREE" -type f | sort | sed 's|^|  |'

if $DRY_RUN; then
    echo "== DRY RUN — publish commands NOT run =="
    echo "  git worktree add --detach build/gh-pages-worktree"
    echo "  (orphan-commit the tree to gh-pages, then:)"
    echo "  git push --force origin <orphan-head>:refs/heads/gh-pages"
    exit 0
fi

echo "== publishing to gh-pages (orphan commit, force) =="
WT="build/gh-pages-worktree"
git worktree remove --force "$WT" 2>/dev/null || true
git worktree prune
# a previous failed run can leave the orphan branch behind; --orphan refuses
# an existing branch name and died silently under the old 2>/dev/null
git branch -D gh-pages-publish 2>/dev/null || true
git worktree add --detach "$WT" >/dev/null
git -C "$WT" checkout --orphan gh-pages-publish
git -C "$WT" rm -rf --quiet . 2>/dev/null || true
cp -a "$TREE"/. "$WT"/
touch "$WT/.nojekyll"
git -C "$WT" add -A
# CI runners have no git identity (empty ident -> exit 128); honor the
# workflow-provided author, fall back to a repo-agnostic one locally.
GIT_AUTHOR_NAME="${APT_GIT_AUTHOR%% <*}"
GIT_AUTHOR_EMAIL="$(printf '%s' "${APT_GIT_AUTHOR#*<}" | tr -d '>')"
GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME"
GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
export GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
git -C "$WT" commit -q -m "APT repository publish $(date -u +%Y-%m-%dT%H:%M:%SZ)

Published by scripts/publish-apt-repo.sh from the packaging milestone v5.0.
Signed with the dedicated repository key
$FP.
Unofficial community tool - not affiliated with Aura Frames Inc."
git -C "$WT" push -q --force origin "HEAD:refs/heads/gh-pages"
git worktree remove --force "$WT"
git worktree prune

echo "published: https://coredmp95.github.io/pushframe/"
echo "user journey lives in README 'Install (Ubuntu/Debian) -> APT repository'."
