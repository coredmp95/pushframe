#!/usr/bin/env bash
# Install-journey proof (phase 22, PYI-01): in a pristine ubuntu:26.04
# container, `uv tool install pushframe` yields a working binary.
#
#   scripts/test-install-journey-container.sh test  <- TestPyPI (rehearsal)
#   scripts/test-install-journey-container.sh live  <- real PyPI
#
# Test mode resolves THIS package from TestPyPI but its dependencies from
# real PyPI (TestPyPI does not host them): --index + --extra-index with
# unsafe-best-match. Assertions: `pushframe --version` equals the repo's
# pyproject version, `pushframe status --help` exits 0.
#
# Sequence: run AFTER the release workflow's TestPyPI (resp. PyPI) upload
# for that version exists — the journey validates a real published artifact.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

MODE="${1:-test}"
case "$MODE" in
    test)
        INSTALL_FLAGS=(--index https://test.pypi.org/simple/
                       --index-strategy unsafe-best-match
                       --extra-index-url https://pypi.org/simple/)
        echo "== journey: install from TestPyPI (deps from real PyPI) =="
        ;;
    live)
        INSTALL_FLAGS=()
        echo "== journey: install from real PyPI =="
        ;;
    *)
        echo "usage: $0 [test|live]" >&2
        exit 64
        ;;
esac

EXPECTED_VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
echo "== expected version: ${EXPECTED_VERSION} =="

exec docker run --rm ubuntu:26.04 bash -euc '
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq curl ca-certificates >/dev/null 2>&1

echo "[1/4] install uv (standalone)"
curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
export PATH="$HOME/.local/bin:$PATH"

echo "[2/4] uv tool install pushframe"
uv tool install pushframe '"${INSTALL_FLAGS[*]}"' >/dev/null
export PATH="$HOME/.local/bin:$PATH"

echo "[3/4] pushframe --version == '"${EXPECTED_VERSION}"'"
INSTALLED=$(pushframe --version | awk "{print \$2}")
test "$INSTALLED" = "'"${EXPECTED_VERSION}"'" || {
    echo "CHECK FAILED [version] got $INSTALLED"; exit 42
}

echo "[4/4] pushframe status --help"
pushframe status --help >/dev/null || { echo "CHECK FAILED [--help]"; exit 43; }

echo "INSTALL JOURNEY PASSED (${MODE})"
'
