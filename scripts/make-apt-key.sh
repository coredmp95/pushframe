#!/usr/bin/env bash
# Generate the dedicated APT-repository signing key (phase 21, D-11).
#
# Idempotent: with a fingerprint file present and the key usable in the
# local keyring, prints the fingerprint and exits 0. If the fingerprint
# file survives but the keyring lost the key (machine migration), re-imports
# from the secret backup. The key has NO passphrase (CI-friendly, D-04);
# the secret material lives OUTSIDE the repo, in ~/.config/pushframe-apt-key/
# (gitignored path pattern).
set -euo pipefail

KEYDIR="${PUSHFRAME_APT_KEY_DIR:-$HOME/.config/pushframe-apt-key}"
UID_NAME="Pushframe APT Repository <deploy@pushframe>"
FP_FILE="$KEYDIR/fingerprint"
SECRET_FILE="$KEYDIR/signing-key.asc"

mkdir -p "$KEYDIR"
chmod 700 "$KEYDIR"

key_usable() {
    [ -s "$FP_FILE" ] && gpg --list-secret-keys "$(cat "$FP_FILE")" >/dev/null 2>&1
}

if key_usable; then
    echo "APT signing key already present: $(cat "$FP_FILE")"
    exit 0
fi

if [ -s "$FP_FILE" ] && [ -s "$SECRET_FILE" ]; then
    # fingerprint recorded but keyring lost the key -> restore from backup
    gpg --import "$SECRET_FILE" >/dev/null 2>&1
    if key_usable; then
        echo "APT signing key restored from backup: $(cat "$FP_FILE")"
        exit 0
    fi
    echo "ERROR: backup import failed; regenerating a fresh key" >&2
fi

echo "== generating dedicated APT signing key (no passphrase) =="
gpg --batch --gen-key <<GPGINPUT
%no-protection
Key-Type: RSA
Key-Length: 2048
Key-Usage: sign
Name-Real: Pushframe APT Repository
Name-Email: deploy@pushframe
Expire-Date: 0
%commit
GPGINPUT

FP=$(gpg --list-secret-keys --with-colons "deploy@pushframe" \
     | awk -F: '/^fpr:/ {print $10; exit}')
if [ -z "$FP" ]; then
    echo "ERROR: key generated but fingerprint not found" >&2
    exit 1
fi

printf '%s\n' "$FP" > "$FP_FILE"
chmod 600 "$FP_FILE"
gpg --armor --export-secret-keys "$FP" > "$SECRET_FILE"
chmod 600 "$SECRET_FILE"

echo "fingerprint: $FP"
echo "secret backup: $SECRET_FILE (gitignored path, keep it safe)"
