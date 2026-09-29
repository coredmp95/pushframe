#!/usr/bin/env bash
# Full APT user-journey proof (phase 21, DEB-05).
#
# 1. publish --dry-run: assembles + signs build/apt-repo (nothing published)
# 2. serves that tree over HTTP from the host
# 3. in a pristine ubuntu:26.04 container, runs the EXACT README journey
#    against the local mirror:
#      keyring (dearmor pushframe.asc) -> sources entry -> apt-get update
#      (MUST stay signature-warning-free) -> apt-get install pushframe
#      -> pushframe status --help
# Only the hostname differs from the live Pages URL; the mechanics (D-10
# layout, D-11 key, D-12 signature chain) are identical.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

PORT=8737
HOST_ALIAS=apt.pushframe.internal   # resolves to the host gateway inside docker
# Optional $1 = an absolute URL (e.g. the live Pages URL) to test against
# instead of the local mirror; publishing must precede the live variant.
JOURNEY_URL="${1:-}"
DOCKER_NET=(--add-host="${HOST_ALIAS}:host-gateway")
if [ -n "$JOURNEY_URL" ]; then
    URL="$JOURNEY_URL"
    DOCKER_NET=()
else
    URL="http://${HOST_ALIAS}:${PORT}"
    ./scripts/publish-apt-repo.sh --dry-run >/dev/null
    echo "== serving build/apt-repo on :${PORT} =="
    python3 -m http.server "$PORT" --bind 0.0.0.0 --directory build/apt-repo \
        >/dev/null 2>&1 &
    SERVER_PID=$!
    trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT
fi
echo "== journey against: $URL =="

docker run --rm "${DOCKER_NET[@]}" ubuntu:26.04 bash -euc "
export DEBIAN_FRONTEND=noninteractive
check() { eval \"\$2\" || { echo \"CHECK FAILED [\$1]\"; exit 42; }; }

apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq gnupg ca-certificates curl >/dev/null 2>&1

echo \"[1/5] keyring: curl asc | gpg --dearmor (README journey)\"
install -d -m 0755 /etc/apt/keyrings
check \"keyring fetch+dearmor\" \"curl -fsSL ${URL}/dists/pushframe.asc | gpg --dearmor -o /etc/apt/keyrings/pushframe.gpg\"
test -s /etc/apt/keyrings/pushframe.gpg

echo \"[2/5] sources entry\"
echo \"deb [signed-by=/etc/apt/keyrings/pushframe.gpg] ${URL} stable main\" \
    > /etc/apt/sources.list.d/pushframe.list

echo \"[3/5] apt-get update — must be signature-clean\"
OUT=\$(apt-get update 2>&1) || { echo \"\$OUT\"; exit 43; }
echo \"\$OUT\" | grep -iE 'W:|GPG|NO_PUBKEY|signature' && {
    echo 'CHECK FAILED [signature warning on our repo]'; exit 44
} || true
echo \"      update clean (no signature warnings)\"

echo \"[4/5] apt-get install pushframe (from the served pool)\"
apt-get install -y pushframe >/dev/null

echo \"[5/5] pushframe runs\"
check \"status --help\" \"pushframe status --help >/dev/null\"
echo \"      pushframe status --help: OK\"
echo \"APT JOURNEY PASSED\"
"
