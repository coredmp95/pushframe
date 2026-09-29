#!/usr/bin/env bash
# Clean-room proof of the pushframe .deb (phase 21, DEB-01..04).
#
# Runs the full acceptance journey in a pristine ubuntu:26.04 container:
#   lintian --fail-on error -> install -> pushframe status --help -> dpkg
#   metadata + disclaimer -> remove -> /usr/lib/pushframe gone -> files in
#   root's AND a regular user's $HOME untouched (DEB-04).
#
# The deb is mounted via the repo root (21-RESEARCH: mount the WORKDIR,
# never the leaf). A fresh container each run -> idempotent by construction.
# Every assertion is named: a failure prints the exact violated check.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

if ! ls dist/pushframe_*_amd64.deb >/dev/null 2>&1; then
    echo "== no deb in dist/, building first =="
    ./scripts/build-deb.sh >/dev/null
fi
DEB=$(ls dist/pushframe_*_amd64.deb | head -1)
echo "== clean-room test of $DEB =="

exec docker run --rm -v "$PWD:/work" -w /work ubuntu:26.04 bash -euc '
export DEBIAN_FRONTEND=noninteractive
check() { eval "$2" || { echo "CHECK FAILED [$1]"; exit 42; }; }

echo "[1/6] lintian --fail-on error"
apt-get update -qq
apt-get install -y -qq ca-certificates lintian >/dev/null
lintian --fail-on error /work/dist/pushframe_*_amd64.deb
echo "      lintian: 0 errors"

echo "[2/6] apt install ./deb"
apt-get install -y /work/dist/pushframe_*_amd64.deb >/dev/null

echo "[3/6] pushframe on PATH + runs (DEB-01)"
check "pushframe on PATH" "command -v pushframe >/dev/null"
pushframe status --help >/dev/null
echo "      pushframe status --help: OK"

echo "[4/6] dpkg metadata + disclaimer (DEB-03)"
check "dpkg metadata" "dpkg -s pushframe | grep -q \"^Package: pushframe$\""
check "section utils" "dpkg -s pushframe | grep -q \"^Section: utils$\""
check "arch amd64" "dpkg -s pushframe | grep -q \"^Architecture: amd64$\""
check "copyright disclaimer" "grep -q \"not affiliated\" /usr/share/doc/pushframe/copyright"
echo "      metadata + disclaimer present"

echo "[5/6] remove"
useradd -m probeuser
touch /root/.marker-root /home/probeuser/.marker-home
apt-get remove -y pushframe >/dev/null

echo "[6/6] removal clean + HOME integrity (DEB-04)"
check "wrapper removed" "test ! -e /usr/bin/pushframe"
check "runtime tree removed" "test ! -d /usr/lib/pushframe"
check "root HOME intact" "test -f /root/.marker-root"
check "user HOME intact" "test -f /home/probeuser/.marker-home"
echo "ALL CONTAINER CHECKS PASSED"
'
