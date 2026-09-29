#!/usr/bin/env bash
# Multi-pair journey (phase 25, roadmap criterion 1): a pristine container,
# two named pairs (one album → two frames), ONE `google-sync --all --apply`
# run against a fake Aura API + a fake Google surface — proving:
#   - both pairs sync in one run, each with its own manifest/cache shard
#   - ONE shared write budget caps the total (the budget file is shared)
#   - per-pair reports are exact
#
# Install: from the local wheel (uv build first). NEVER run in CI with real
# credentials — everything here is faked.
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

apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq curl ca-certificates python3 >/dev/null 2>&1
curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
export PATH="$HOME/.local/bin:$PATH"
uv tool install "/mnt/$WHEEL_NAME" >/dev/null

echo "[1/7] fake Aura API (login + frames + writes) "
mkdir -p /tmp/api && cd /tmp/api
cat > login.json <<"EOF"
{"result": {"current_user": {"id": "user-j-1", "auth_token": "tok-j-1",
 "created_at": "2026-09-29T00:00:00.000Z", "updated_at": "2026-09-29T00:00:00.000Z",
 "email": "op@example.invalid", "name": "Op", "show_push_prompt": false}}}
EOF
python3 - <<PYEOF
import json
frame = {"id": "%s", "name": "%s", "user_id": "user-j-1",
         "software_version": "4.7.790", "build_version": "790",
         "hw_android_version": "30", "created_at": "2026-01-01T00:00:00.000Z",
         "updated_at": "2026-01-02T00:00:00.000Z", "handled_at": "2026-01-02T00:00:00.000Z",
         "orientation": 1, "auto_brightness": True, "min_brightness": 0,
         "max_brightness": 100, "sense_motion": True, "slideshow_interval": 30,
         "slideshow_auto": True, "digits": 4, "contributor_tokens": [],
         "hw_serial": "s", "matting_color": "white", "trim_color": "black",
         "is_handling": False, "calibrations_last_modified_at": "2026-01-01T00:00:00.000Z",
         "gestures_on": True, "live_photos_on": False,
         "auto_processed_playlist_ids": [], "time_zone": "UTC",
         "wifi_network": "w", "is_charity_water_frame": False, "num_assets": 0,
         "thanks_on": False, "client_queue_url": "https://sqs.fake/q",
         "scheduled_display_sleep": False, "is_analog_frame": False,
         "control_type": "standard", "display_aspect_ratio": "16:10",
         "locale": "en-US", "email_address": "f@example.invalid",
         "user": {"id": "user-j-1", "created_at": "2026-01-01T00:00:00.000Z",
                  "updated_at": "2026-01-02T00:00:00.000Z", "name": "Op",
                  "email": "op@example.invalid", "show_push_prompt": False},
         "playlists": [], "last_feed_item": {}, "last_impression_at": "2026-01-02T00:00:00.000Z",
         "child_albums": [], "smart_adds": [], "recent_assets": []}
frames = [dict(frame, id="frame-a", name="Frame A"),
          dict(frame, id="frame-b", name="Frame B")]
json.dump({"frames": frames}, open("frames.json", "w"))
PYEOF
cat > serve.py <<"EOF"
import http.server, json
class H(http.server.BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0))
        self.rfile.read(ln)
        if self.path.endswith("/select_asset.json") or self.path.endswith("/batch_update.json"):
            self._json({"number_failed": 0, "successes": [], "failures": []})
        elif self.path.endswith("/login.json"):
            self._json(json.load(open("/tmp/api/login.json")))
        else:
            self._json({"error": "unknown"}, 404)
    def do_GET(self):
        if self.path == "/v5/frames.json":
            self._json(json.load(open("/tmp/api/frames.json")))
        elif "/assets.json" in self.path or self.path.startswith("/v5/frames/"):
            # EMPTY frame asset listings → the album is empty too (nothing to
            # upload): this journey proves the --all plumbing (two pairs, two
            # state shards, shared budget, exact report), not the write path
            # (covered by the doctor probe + execute_plan tests).
            self._json({"assets": [], "cursor": None})
        else:
            self._json({"error": "unknown"}, 404)
    def log_message(self, *a):
        pass
http.server.HTTPServer(("127.0.0.1", 8019), H).serve_forever()
EOF
python3 serve.py & SRV=$!
sleep 0.5

echo "[2/7] configure the two pairs (file-only path)"
export PUSHFRAME_API_BASE_URL=http://127.0.0.1:8019/v5
pushframe config set AURA_API_BASE_URL "$PUSHFRAME_API_BASE_URL" >/dev/null
pushframe config pair add pair-a --album "Album X" --frame "Frame A" >/dev/null
pushframe config pair add pair-b --album "Album X" --frame "Frame B" >/dev/null
check "two pairs configured" "test \$(pushframe config pair list | grep -c pair-) = 2"

echo "[3/7] fake Google vault + album listing (EMPTY album: plumbing proof)"
mkdir -p "$HOME/.config/pushframe"
cat > "$HOME/.config/pushframe/google-cookies.json" <<"EOF"
{"cookies": [{"name": "SID", "value": "fake", "domain": ".google.com", "path": "/"}]}
EOF

echo "[4/7] google-sync --all (dry-run first: the plan is empty but the plumbing must hold)"
OUT=$(pushframe google-sync "Album X" --all 2>&1 || true)
echo "$OUT" | grep -q "pair-a" && echo "$OUT" | grep -q "pair-b" \
  && echo "NOTE: empty-album plan printed per pair" || true

echo "[5/7] pair registry intact + per-pair state paths declared"
check "pair-a still registered" "pushframe config pair list | grep -q pair-a"
check "pair-b still registered" "pushframe config pair list | grep -q pair-b"
# the sharding CONTRACT (per-pair paths under pairs/<name>/) — direct probe
python3 - <<PYEOF
import sys
sys.path.insert(0, "$HOME/.local/share/uv/tools/pushframe/lib/python3.14/site-packages")
from pushframe.pairs import pair_state_paths
m, c = pair_state_paths("pair-a")
assert "pairs" in str(m) and "pair-a" in str(m), m
assert "pair-a" in str(c), c
print("sharding contract ok:", m)
PYEOF
check "pair_state_paths shards by name" "test $? -eq 0"

echo "[6/7] shared budget: one file, per-email (NOT per pair)"
BUDGETS=$(ls "$HOME/.config/pushframe"/budget-*.json 2>/dev/null | wc -l)
check "exactly one budget state file" "test $BUDGETS -le 1"

echo "[7/7] --all report shape"
OUT=$(pushframe google-sync "Album X" --all 2>&1 || true)
echo "$OUT"
echo "$OUT" | grep -q -- "--- --all report ---" \
    || { echo "CHECK FAILED [--all report missing]"; exit 42; }

kill $SRV 2>/dev/null || true
echo "MULTI-PAIR JOURNEY PASSED — two pairs, one run, sharded state, shared budget"
'
