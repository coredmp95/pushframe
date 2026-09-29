#!/usr/bin/env bash
# Config-wizard journey (phase 23, roadmap criterion 1): a pristine
# ubuntu:26.04 container with NO credentials anywhere proves the full
# file-only path:
#
#   1. a fake Aura API serves /v5/login.json + /v5/frames.json
#   2. `pushframe config` (piped answers) logs in against the fake,
#      stores email + auth_token in ~/.config/pushframe/config.json (0600)
#   3. `pushframe status` with an EMPTIED environment (env -i) still lists
#      the frame — resuming the stored session
#   4. the fake API log proves status NEVER hit login.json (zero password
#      traffic after the wizard)
#
# Installs from the local wheel: `scripts/build-deb.sh`'s uv build or a
# plain `uv build` must have produced dist/pushframe-*.whl beforehand.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

WHEEL=$(ls dist/pushframe-*.whl 2>/dev/null | head -1) || true
[ -n "$WHEEL" ] || { echo "no dist/pushframe-*.whl — run: uv build" >&2; exit 64; }

exec docker run --rm \
    -e WHEEL_NAME="$(basename "$WHEEL")" \
    -v "$(pwd)/$WHEEL:/mnt/$(basename "$WHEEL"):ro" \
    ubuntu:26.04 bash -euc '
export DEBIAN_FRONTEND=noninteractive
check() { eval "$2" || { echo "CHECK FAILED [$1]"; exit 42; }; }

echo "[1/6] uv standalone (hermetic, same as the .deb)"
apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq curl ca-certificates python3 >/dev/null 2>&1
curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
export PATH="$HOME/.local/bin:$PATH"

echo "[2/6] install the local wheel"
uv tool install "/mnt/$WHEEL_NAME" >/dev/null

echo "[3/6] fake Aura API (login + frames only)"
mkdir -p /tmp/api && cd /tmp/api
cat > login.json <<"EOF"
{"result": {"current_user": {"id": "user-j-1", "auth_token": "journey-token-24",
 "created_at": "2026-09-29T00:00:00.000Z", "updated_at": "2026-09-29T00:00:00.000Z",
 "email": "operator@example.invalid", "name": "Operator", "show_push_prompt": false}}}
EOF
cat > frames.json <<"EOF"
{
  "frames": [
    {
      "id": "frame-fake-0001",
      "name": "Fake Frame",
      "user_id": "user-fake-0001",
      "software_version": "4.7.790",
      "build_version": "790",
      "hw_android_version": "30",
      "created_at": "2023-01-01T00:00:00.000Z",
      "updated_at": "2023-01-02T00:00:00.000Z",
      "handled_at": "2023-01-02T00:00:00.000Z",
      "orientation": 1,
      "auto_brightness": true,
      "min_brightness": 0,
      "max_brightness": 100,
      "sense_motion": true,
      "slideshow_interval": 30,
      "slideshow_auto": true,
      "digits": 4,
      "contributor_tokens": [],
      "hw_serial": "fake-serial-0001",
      "matting_color": "white",
      "trim_color": "black",
      "is_handling": false,
      "calibrations_last_modified_at": "2023-01-01T00:00:00.000Z",
      "gestures_on": true,
      "live_photos_on": false,
      "auto_processed_playlist_ids": [],
      "time_zone": "America/New_York",
      "wifi_network": "fake-wifi",
      "is_charity_water_frame": false,
      "num_assets": 2,
      "thanks_on": false,
      "client_queue_url": "https://sqs.fake.invalid/fake-queue",
      "scheduled_display_sleep": false,
      "is_analog_frame": false,
      "control_type": "standard",
      "display_aspect_ratio": "16:10",
      "locale": "en-US",
      "email_address": "frame-fake@example.invalid",
      "user": {
        "id": "user-fake-0001",
        "created_at": "2023-01-01T00:00:00.000Z",
        "updated_at": "2023-01-02T00:00:00.000Z",
        "name": "Fake Tester",
        "email": "fake-user@example.invalid",
        "show_push_prompt": false
      },
      "playlists": [],
      "last_feed_item": {},
      "last_impression_at": "2023-01-02T00:00:00.000Z",
      "child_albums": [],
      "smart_adds": [],
      "recent_assets": []
    }
  ]
}

EOF
cat > serve.py <<"EOF"
import http.server, json, sys
class H(http.server.BaseHTTPRequestHandler):
    def _send(self, path):
        open("/tmp/api/hits.log", "a").write(path + "\n")
        body = open("/tmp/api" + path).read().encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        if self.path == "/v5/login.json":
            self._send("/login.json")
        else:
            self.send_error(404)
    def do_GET(self):
        if self.path == "/v5/frames.json":
            self._send("/frames.json")
        else:
            self.send_error(404)
    def log_message(self, *a):
        pass
srv = http.server.HTTPServer(("127.0.0.1", 8018), H)
import threading
threading.Thread(target=srv.serve_forever, daemon=True).start()
import time
try:
    while True:
        time.sleep(0.2)
except KeyboardInterrupt:
    pass
EOF
python3 serve.py & SRV=$!
sleep 0.5

echo "[4/6] pushframe config — wizard against the fake API (pty driver: it is interactive by design)"
export PUSHFRAME_API_BASE_URL=http://127.0.0.1:8018/v5
cat > drive.py <<"PYEOF"
import os, pty, sys, time
pid, fd = pty.fork()
if pid == 0:
    os.execvp("pushframe", ["pushframe", "config"])
time.sleep(1.5)
os.write(fd, b"operator@example.invalid\n")
time.sleep(0.8)
os.write(fd, b"hunter2-nothing-real\n")
time.sleep(0.8)
os.write(fd, b"1\n")
time.sleep(0.5)
os.write(fd, b"n\n")
out = b""
while True:
    try:
        chunk = os.read(fd, 4096)
    except OSError:
        break
    if not chunk:
        break
    out += chunk
sys.stdout.write(out.decode(errors="replace"))
_, status = os.waitpid(pid, 0)
sys.exit(os.waitstatus_to_exitcode(status))
PYEOF
python3 drive.py
check "wizard exited 0" "test $? -eq 0"
MODE=$(stat -c %a "$HOME/.config/pushframe/config.json")
check "config is 0600" "test $MODE = 600"
grep -q journey-token-24 "$HOME/.config/pushframe/config.json"
check "token stored" "test $? -eq 0"
if grep -q hunter2 "$HOME/.config/pushframe/config.json"; then
    echo "CHECK FAILED [plaintext password stored]"; exit 42
fi
# Everything the env-free run needs goes into the FILE from here: the fake
# API root itself (settings resolve file-first too — 100% file-only run).
pushframe config set AURA_API_BASE_URL http://127.0.0.1:8018/v5 >/dev/null
check "config set exited 0" "test $? -eq 0"

echo "[5/6] status with an EMPTIED environment (no PATH leak, NO env vars at all)"
OUT=$(env -i PATH="$HOME/.local/bin:/usr/bin:/bin" HOME="$HOME" pushframe status)
echo "$OUT"
echo "$OUT" | grep -q "Logged in as operator@example.invalid"
check "status resumes stored session" "test $? -eq 0"
echo "$OUT" | grep -q "Fake Frame"
check "frame listed" "test $? -eq 0"
echo "$OUT" | grep -q journey-token-24 && { echo "CHECK FAILED [token printed]"; exit 42; } || true

echo "[6/6] fake API hit log — status must NOT have logged in"
kill $SRV; sleep 0.5
LOGIN_HITS=$(grep -c "/login.json" /tmp/api/hits.log)
check "login.json hit exactly once (wizard only)" "test $LOGIN_HITS = 1"
FRAMES_HITS=$(grep -c "/frames.json" /tmp/api/hits.log)
check "frames.json hit twice (wizard + status)" "test $FRAMES_HITS = 2"

echo "CONFIG JOURNEY PASSED — file-only path works, status never logs in"
'
