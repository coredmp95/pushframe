#!/usr/bin/env bash
# Build the pushframe Debian package (phase 21, DEB-01..03).
#
# Layout (D-02/D-03): the deb embeds a HERMETIC runtime — uv's standalone
# CPython 3.14 + the pushframe package and its dependencies, all under
# /usr/lib/pushframe/python/ — plus a single POSIX-sh wrapper
# /usr/bin/pushframe. The distro's python is never touched or depended upon.
#
# Version single source (D-08): read from pyproject.toml, nothing else.
#
# Container/lintian evidence baked in (2026-09-29 clean-room runs on
# ubuntu:26.04):
#   * Architecture: amd64 — the embedded interpreter is an x86-64 binary;
#     `all` made lintian flag every .so (arch-independent-contains-binary).
#     Depends gains libc6 (the interpreter is a dynamic ELF; lintian
#     missing-dependency-on-libc was the last error).
#   * site-packages console-script shebangs are rewritten from the build
#     path to the runtime path (uv writes the absolute staging path).
#   * Permissions normalized LAST (dirs 755; shebang scripts 755; everything
#     else 644 — first run normalized before usr/share/ existed and flattened
#     exec bits off tk demos: script-not-executable + 0775 residuals).
#   * A real changelog.gz — a native-looking package without one is a
#     lintian error.
#   * lintian overrides for the classes inherent to shipping a private
#     CPython (embedded-library, unstripped, RPATH, timestamped gzip,
#     hardening-no-pie, ...) — each entry carries the reason, see
#     packaging/lintian-overrides in the staging tree.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
STAGE="build/deb"
# Output dir override (phase 22 D-06): the release workflow builds python
# artifacts (uv build -> dist/) and the deb in the SAME run; without this
# the deb build's `rm -rf dist` would clobber the wheel/sdist. Default
# unchanged for local use.
OUT_DIR="${PUSHFRAME_DEB_OUT_DIR:-dist}"
OUT="$OUT_DIR/pushframe_${VERSION}_amd64.deb"

echo "== pushframe deb build: version ${VERSION} =="

rm -rf "$STAGE" "$OUT_DIR"
mkdir -p "$STAGE/usr/lib/pushframe" "$STAGE/usr/bin" "$STAGE/DEBIAN" "$OUT_DIR"

# --- 1. standalone CPython 3.14 into the staging tree (D-03) ---------------
# NB: `uv python find` resolves the PROJECT .venv first — we must target the
# MANAGED standalone install explicitly (uv data dir), never a venv (a venv's
# interpreter symlinks into its host and does not survive relocation; probe-
# proven 2026-09-29).
echo "== provisioning standalone python 3.14 =="
uv python install 3.14 >/dev/null 2>&1 || true
UV_PYTHON_DIR="${UV_PYTHON_INSTALL_DIR:-$HOME/.local/share/uv/python}"
PY_SRC=$(ls -d "$UV_PYTHON_DIR"/cpython-3.14*-linux-x86_64-gnu 2>/dev/null | sort -V | tail -1)
if [ -z "$PY_SRC" ]; then
    echo "ERROR: no standalone cpython-3.14 found under $UV_PYTHON_DIR" >&2
    exit 1
fi
echo "   from: $PY_SRC"
cp -a "$PY_SRC" "$STAGE/usr/lib/pushframe/python"
PYROOT="$STAGE/usr/lib/pushframe/python"

# prune what a runtime package never needs (probe prunes + lintian findings:
# idlelib pulled a python3-script-but-no-python3-dep error; ensurepip/pip are
# seeds for a buildable env, which a frozen runtime is not; include/ and
# config-3.14*/ only matter when compiling C extensions against this python).
echo "== pruning build-only and idle trees =="
rm -rf "$PYROOT/include" "$PYROOT/lib/pkgconfig" \
       "$PYROOT/lib/python3.14/config-3.14"* \
       "$PYROOT/lib/python3.14/idlelib" \
       "$PYROOT/lib/python3.14/ensurepip"
rm -f "$PYROOT/bin/idle3" "$PYROOT/bin/idle3.14" \
      "$PYROOT/bin/pip" "$PYROOT/bin/pip3" "$PYROOT/bin/pip3.14" \
      "$PYROOT/bin/pydoc" "$PYROOT/bin/pydoc3" "$PYROOT/bin/pydoc3.14"
find "$PYROOT" -type d \( -name "__pycache__" -o -name "test" -o -name "tests" \) \
     -prune -exec rm -rf {} + 2>/dev/null || true

# --- 2. install the project + dependencies into that interpreter -----------
echo "== installing pushframe + deps into the embedded interpreter =="
# The uv-managed standalone marks itself externally-managed; a package build is
# exactly the sanctioned exception — install into it like uv would into a venv.
# (venv-on-top would add a second indirection; --target into the managed tree's
# site-packages keeps ONE interpreter tree, simplest to relocate.)
SITE_DIR="$PYROOT/lib/python3.14/site-packages"
uv pip install --python "$PYROOT/bin/python3.14" . --quiet \
    --target "$SITE_DIR" --break-system-packages
rm -rf "$SITE_DIR/pip" "$SITE_DIR/pip-"* "$SITE_DIR/setuptools"* 2>/dev/null || true
find "$PYROOT" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# uv writes console-script shebangs as the ABSOLUTE build path
# (…/build/deb/usr/lib/...) — lintian flags it (unusual-interpreter) and the
# scripts would break if the stage were moved. Rewrite to the runtime path.
for f in "$SITE_DIR"/bin/*; do
    [ -f "$f" ] || continue
    if [ "$(head -c2 "$f")" = "#!" ]; then
        sed -i "1s|^#!.*|#!/usr/lib/pushframe/python/bin/python3.14|" "$f"
    fi
done

# --- 2.5 pre-compile bytecode (package-owned .pyc) --------------------------
# Ships the .pyc inside the deb: faster startup AND every bytecode file is
# dpkg-owned -> removed cleanly. Pairs with PYTHONDONTWRITEBYTECODE in the
# wrapper so nothing new is ever written into the system tree.
echo "== pre-compiling bytecode (package-owned) =="
"$PYROOT/bin/python3.14" -m compileall -q \
    "$PYROOT/lib/python3.14" 2>&1 | grep -v "^Compiling" || true
find "$PYROOT" -type d -name "__pycache__" -exec chmod 755 {} +

# --- 3. /usr/bin/pushframe wrapper (D-06) -----------------------------------
cat > "$STAGE/usr/bin/pushframe" <<'EOF'
#!/bin/sh
# PYTHONDONTWRITEBYTECODE: the system tree must never gain runtime-owned
# files — a stray __pycache__ would make dpkg leave /usr/lib/pushframe
# behind on removal (clean-room finding 2026-09-29). Bytecode is shipped
# pre-compiled and package-owned instead.
exec env PYTHONDONTWRITEBYTECODE=1 /usr/lib/pushframe/python/bin/python3.14 -m pushframe.cli "$@"
EOF

# --- 4. DEBIAN control files (D-07) + doc/lintian trees ---------------------
INSTALLED_SIZE=$(du -sk "$STAGE/usr" | cut -f1)
sed -e "s/@VERSION@/${VERSION}/" -e "s/@INSTALLED_SIZE@/${INSTALLED_SIZE}/" \
    packaging/control-template > "$STAGE/DEBIAN/control"
install -m 755 packaging/postinst "$STAGE/DEBIAN/postinst"
mkdir -p "$STAGE/usr/share/doc/pushframe" "$STAGE/usr/share/lintian/overrides"
install -m 644 packaging/pushframe.copyright "$STAGE/usr/share/doc/pushframe/copyright"
install -m 644 packaging/lintian-overrides \
    "$STAGE/usr/share/lintian/overrides/pushframe"

# changelog: a native-looking package without usr/share/doc/<pkg>/changelog.gz
# is a lintian ERROR; ship a real Debian-format one, date = build date.
cat > "$STAGE/usr/share/doc/pushframe/changelog" <<EOF
pushframe (${VERSION}) unstable; urgency=medium

  * Initial Debian packaging of the pushframe CLI (v5.0 packaging milestone).

 -- Fabrice DIDIERJEAN <coredmp95@gmail.com>  $(date -R)
EOF
gzip -9n "$STAGE/usr/share/doc/pushframe/changelog"

# --- 5. Debian-canonical permissions (run LAST — everything must exist) -----
# uv/PSA leave 775 dirs and exec bits on plain .py files (lintian
# non-standard-*-perm, executable-not-elf-or-script). Canonical rule:
#   dirs 755; files with a shebang 755 (real scripts — tk demos, console
#   scripts); everything else 644 (.so are dlopen'd, exec not needed —
#   Debian ships them 644). bin/ keeps exec outright (the interpreter is
#   executed directly).
echo "== normalizing permissions =="
find "$STAGE/usr" -type d -exec chmod 755 {} +
while IFS= read -r -d '' f; do
    case "$f" in "$PYROOT/bin/"*) chmod 755 "$f"; continue ;; esac
    if [ "$(head -c2 "$f")" = "#!" ]; then chmod 755 "$f"; else chmod 644 "$f"; fi
done < <(find "$STAGE/usr" -type f -print0)

# --- 6. build ----------------------------------------------------------------
echo "== dpkg-deb --build =="
dpkg-deb --build --root-owner-group "$STAGE" "$OUT"
ls -lh "$OUT"
echo "== artifact: $OUT =="
