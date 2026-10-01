# `pushframe` — Command Reference

Complete reference for every command, flag, and exit code, with real output.

`pushframe` talks to the Aura **cloud** API (`api.pushd.com/v5`) plus AWS S3/SQS. It never
talks to the frame over your local network — everything goes through your Aura account.

> The API is unofficial and reverse-engineered. It is undocumented and can change without
> notice. Every write path here has been exercised against a live frame, but that is a
> snapshot, not a guarantee.

## Contents

- [Global usage](#global-usage)
- [`doctor`](#doctor--can-this-machine-write-today)
- [Pairs](#pairs--one-album--several-frames-and-back)
- [Scheduling](#scheduling--systemd-user-timers-no-root)
- [`config`](#config--set-up-credentials-and-settings-once)
- [`status`](#status--check-credentials-and-list-frames)
- [`inspect`](#inspect--look-at-one-frame)
- [`sync`](#sync--make-a-frame-match-a-directory)
- [`push`](#push--upload-only-never-removes)
- [`reconcile`](#reconcile--account-for-stuck-placeholder-rows)
- [Google commands (`google-link` / `google-album` / `google-sync`)](#google-commands)
- [Choosing between `sync` and `push`](#choosing-between-sync-and-push)
- [Environment variables](#environment-variables)
- [Exit codes](#exit-codes)
- [Known issues](#known-issues)

## Global usage

One session path (phase 24): every command authenticates the same way —
`PUSHFRAME_EMAIL`/`PUSHFRAME_PASSWORD` env (override; discouraged for
humans), else the **stored token session** from `pushframe config` (no
login call, no password), else ONE interactive password prompt whose token
is persisted (the password is never written anywhere). Non-interactive runs
never prompt: without any credential they fail named with the remedy.

On an expired token: a terminal session prompts once and continues; a
scheduled run reports `SessionExpiredError` with the remedy.

### `pushframe logout`

Deletes the stored session token — and only the token. Your email and
settings stay (the next `pushframe config` proposes your email). Idempotent;
token material is never printed.

```
usage: pushframe [-h] [--version] [--debug]
                 {status,logout,doctor,config,inspect,sync,push,reconcile,
                  google-link,google-album,google-sync,schedule} ...

options:
  -h, --help  show this help message and exit
  --version   show the program version (e.g. "pushframe 5.1.6") and exit
  --debug     Show verbose loguru request/response logging on stderr
```

`--version` prints the running release and exits 0 — the first
distribution release is 5.0.0; every install channel (deb / APT /
uv tool) ships the same version.

`--debug` sits on the root parser, so it goes **before** the subcommand:

```bash
uv run pushframe --debug status      # correct
uv run pushframe status --debug      # error: unrecognized argument
```

Without `--debug` the CLI is quiet: normal runs print only the report. With it, every HTTP
request and response is logged to stderr (secrets are redacted). A full log of every run is
always written to `logs/file_{timestamp}.log` regardless of the flag.

### The `--frame` argument

`--frame` accepts a **case-insensitive substring of the frame name**, or an exact frame id:

```bash
uv run pushframe inspect --frame "living"                                  # substring
uv run pushframe inspect --frame "00000000-0000-0000-0000-000000000000"    # exact id
```

Resolution rules:

| Situation | Behaviour |
|---|---|
| Exactly one name matches | Resolved |
| More than one name matches | **Ambiguous** — the run stops and lists the matches. The id fallback is *not* attempted. |
| No name matches | Falls back to an exact, case-sensitive id match |
| Still nothing | Not found — every frame on the account is listed so you can pick |

Ambiguity is never resolved silently, so a loose substring can't quietly target the wrong
frame.

## `doctor` — can this machine write today?

The pre-flight gate for any long write run. Offline tests and install
journeys cannot prove a real WRITE lands on a real frame from THIS machine,
THIS IP, today — the anti-abuse layer can be scoped per-IP/per-surface
(observed in production, 2026-09: reads green, writes 401). Doctor probes exactly that,
deliberately, with one 4×4 test image:

```bash
pushframe doctor            # session → frames → WRITE probe → verify → cleanup
pushframe doctor --no-write # session + frames reads only (zero writes)
pushframe doctor --frame "Living Room"
```

Five checks, one unambiguous verdict: **GO** (writes work from here, now —
launch the sync) or **NO-GO** with the captured 401 server body and its
classification (anti-abuse trip vs token problem). Charges ~6 write-budget
tokens; safe to repeat; never run it in CI.

### When the anti-abuse layer refuses your writes

Pushd's anti-abuse layer can refuse writes while reads keep working
(proven in production, 2026-09). It has two shapes — learn both, the disguised
one is the trap:

| Surface | Shape | Server body |
|---|---|---|
| `login.json` | **HTTP 475** — explicit lockout | "Aura API is rate-limiting or has locked out this account" |
| everything else — **token included** | **HTTP 401** — the trip in disguise | `{"error": true, "message": "Request Unauthenticated", "logout": true}` |

The 401 arrives on a FRESH token, seconds after a successful login: it
means "we refuse this client's writes (trip)", not "your token is bad".
The trip is **account-wide** — not scoped to the session, the endpoint
that provoked it, or the login that just succeeded — and it can spread
to more surfaces when provoked (observed: writes-only 401 first; after
probe activity, `login.json` itself went 475).

**Never re-login, never retry.** Every call that touches a tripped
surface re-arms the clock, and a fresh login bypasses nothing — the
trip is not session-scoped. One documented counter-evidence: 8 doctor probes
spread over 7 hours, all 475, each one buying another wait — the probes
were the reason nothing cleared. pushframe already stops on the FIRST
trip-shaped refusal (`TripDetectedError` — no retry, no re-login); the
discipline is yours to keep outside the tool.

The drill:

1. `pushframe doctor` before any sync (GO = the write surface is open NOW)
2. Trip detected → wait **60+ min** from the stop, then `pushframe doctor` again — **once**. A second tripped doctor is the signal to stop doctoring (every probe re-arms the clock) and escalate to the 24-hour protocol below.
3. If doctor passes but a sync still trips immediately, lower the per-batch
   volume — the trip keys on batch size (a 1-item write may pass where a
   50-item chunk is refused):
   ```bash
   pushframe google-sync family --frame "Living Room" --apply --yes --batch-size 10
   ```

#### The 24-hour probe protocol (escalated recovery)

When 60+ minutes does not clear the trip (observed, 2026-09-30: still
tripped after a ~5-hour silence), stop probing and switch to days:

1. **Total silence.** No pushframe command of any kind against the
   account — and nothing automated either: a timer or a loop probing
   hourly keeps the account tripped indefinitely.
2. **After 24h+ of silence, ONE probe — a read, never a doctor.**
   `pushframe status` resumes the stored token session (no login call,
   no write). Doctor is the WRONG first probe here: its write attempt
   re-arms the clock even while it tells you the truth.
3. **Probe green** (reads answer) → ONE `pushframe doctor` as the single
   write probe. GO → run the sync with a lowered batch size (step 3
   above). Probe trips → back to step 1; next probe in another 24h.

Never two probes in one 24h bucket. The wait has no measured shape —
what is measured is only that hours do not cut it, and every premature
call restarts it.

**Client identity matters.** Pushd's anti-abuse layer also fingerprints
the client itself: a years-stale `Aura/4.7.790` user agent and the
all-zeros `0000000000000000` device identifier shared by every pushframe
installation read as "not a phone" (2026-09-30: reads stayed green for
the real phone app while writes were 401-refused for months). Give each
install its own identity once:

```bash
pushframe config set DEVICE_IDENTIFIER "$(uuidgen)"
```

The user agent defaults to a current Play build (a setting since 5.1.1);
bump it with `pushframe config set USER_AGENT 'Aura/…'` if it ages again.

Progress is never lost: only confirmed writes are remembered, and the next
run uploads the remainder once — never twice.

## Pairs — one album → several frames (and back)

Named album↔frame mappings live in the config (phase 25):

```bash
pushframe config pair add family --album family --frame "Living Room"
pushframe config pair list          # name → album → frame (+ state paths)
pushframe config pair remove family
```

Run one pair or every pair:

```bash
pushframe google-sync "Album X" --pair family --apply --yes
pushframe google-sync "Album X" --all --apply --yes
```

`--all` runs every pair in sorted-name order with **one shared write
budget** capping the total (account-level SAFE-02). A failing pair is
reported and never blocks the others; exit 1 if any pair failed. Mirror
state is **per pair** (manifest under `~/.config/pushframe/pairs/<name>/`,
cache under `~/.local/state/pushframe/pairs/<name>/cache/`) — two pairs
never share dedupe memory.

## Scheduling — systemd USER timers (no root)

```bash
pushframe schedule add nightly --pair family --every 1d
pushframe schedule list
pushframe schedule remove nightly
```

**What gets scheduled:** the timer runs a `google-sync` **of a Google Photos
album onto a frame** — with `--pair NAME` it resolves the named pair (album →
frame) and executes `pushframe google-sync "<album>" --frame "<frame>" --apply
--yes --scheduled` at every tick: the album is mirrored onto the frame, exactly
as a manual run. `--album A --frame F` schedules the same run without a named
pair; `--sync-dir DIR --frame F` schedules the local-directory `sync` instead;
`--at "OnCalendar"` (e.g. `"Mon *-*-* 02:00"`) replaces `--every Nmin|Nh|Nd`.
Everything comes from stored config (token session, pair spec) — a timed run
NEVER prompts. `pushframe schedule --help` prints the same summary with
examples. `Restart=no` on purpose: on failure (including the
anti-abuse trip, which pushframe detects and stops at the first refusal)
the unit just ends; the next tick is the retry. Logs land in
`~/.local/state/pushframe/<job>.log`.

Two safety properties in scheduled mode:

- **SAFE-02 flips to skip-and-log**: a plan that would mass-hide over the
  threshold is NOT applied — the run logs `SKIPPED (--scheduled)` and exits
  0 (review manually; the unit must not fail over a safety decision).
- **Headless hosts** (servers, boxes without a desktop): timers fire without an active session
  only if you enable lingering once:
  `loginctl enable-linger $USER` (run it yourself; the tool never does).

## `config` — set up credentials and settings once

The conversational alternative to hand-managed environment variables. Every value
still resolves live with the precedence **environment → config file → default**;
the file is `~/.config/pushframe/config.json`, mode `0600`, written atomically.

### The wizard (`pushframe config`)

On a fresh install it asks for your email, then your password (**hidden
input**), then **verifies the login against the real Aura API before writing
anything** — a failed login writes nothing. On an install that already has an
email stored, **pressing Enter keeps the stored email** — the banner says so and
means it: with a stored session token, a second Enter reuses the token and the
wizard finishes WITHOUT any API call (the anti-abuse rule: no gratuitous
login); only typing a NEW email moves to the password question. On success it
stores the email plus the session `auth_token` (not the password; a stored
token is silently refreshed at re-login), then offers the optional questions
(country for the geo guard, budget tuning) with current effective values as
defaults.

Refuses to run when stdin is not a terminal — scheduled jobs have nothing to
interact with; feed them the environment or the config file instead.

After a successful wizard run, `status` works with **no environment variables
at all**: with no `PUSHFRAME_EMAIL`/`PUSHFRAME_PASSWORD` set it resumes the
stored session (auth headers only — **no login call**, no password traffic).
Proof: `scripts/test-config-journey-container.sh` runs the whole wizard →
status flow in a pristine container against a fake API and asserts the API
saw exactly one login.

### `config show`

Prints every known setting: its **effective value** (secrets masked as `***`),
and **where it came from** (`env`, `file`, or `default`). Lists the keys an
environment variable is currently shadowing, so a stale export never silently
beats what you put in the file.

### `config import FILE`

Adopts an existing `.env` (or any file of `KEY=VALUE` lines). Values that the
environment already provides are **skipped** (env would shadow them anyway) and
reported as such; the rest is validated against the known-key whitelist and
stored. Nothing is stored that would be ignored at resolve time.

### `config path` / `config set KEY VALUE` / `config get KEY`

Direct access: print the config file path; set one key (validated, whitelist,
atomic save); print one resolved value (`get` reflects the true precedence —
it is the value a command would actually use).

### `config pair add|list|remove`

Named album↔frame mappings consumed by `google-sync --pair` / `--all` and by
the systemd timers: `add NAME --album A --frame F`, `list` (name → album →
frame + state paths), `remove NAME`. Full semantics in
[Pairs](#pairs--one-album--several-frames-and-back).

## `status` — check credentials and list frames

```
usage: pushframe status [-h]
```

The first command to run. With env credentials it logs in fresh; with a stored token session
it resumes it (no login call) and lists your frames.
Nothing is ever written except a session token: if the stored token is
refused (HTTP 401), an interactive run offers ONE re-login right there and
continues — a refused fresh login is never retried (that shape is the
anti-abuse trip on reads: 24-hour silence); a non-interactive run fails
named with the `pushframe config` remedy instead of prompting. `inspect`,
`sync`, `push` and `google-sync` read frames through the same gate, so
this contract holds on every frames-reading verb.

```bash
uv run pushframe status
```

```
PUSHFRAME_EMAIL: set
PUSHFRAME_PASSWORD: set
Logged in as you@example.com
1 frames:
  - Living Room (id: 00000000-0000-0000-0000-000000000000)
```

The credential check runs **before** any network call, and prints only `set` / `NOT SET` —
never the password itself. If either is missing, it stops there and exits `1`:

```
PUSHFRAME_EMAIL: set
PUSHFRAME_PASSWORD: NOT SET
```

## `inspect` — look at one frame

```
usage: pushframe inspect [-h] --frame FRAME

options:
  --frame FRAME  Frame name (substring) or id
```

Read-only. Shows the frame, its owner, contributors, and the first 10 photos.

```bash
uv run pushframe inspect --frame "Living Room"
```

```
Frame: Living Room (id: 00000000-0000-0000-0000-000000000000)
Owner: Your Name <you@example.com>
Contributors (0):
Assets: 172
Photos (showing 10 of 154, API order):
  - a1b2c3d4-1111-11f1-8000-0aaaaaaaaaaa | b5c6d7e8-2222-4333-9444-0bbbbbbbbbbb.jpg | 2026-07-04 19:41:13.922000
  - c9d0e1f2-3333-7444-8555-0ccccccccccc | None | None
Placeholder rows: 58 (run `pushframe reconcile --frame ...` for detail)
```

Two things in that output are worth understanding, and both are server-side quirks rather
than bugs in this client — see [Known issues](#known-issues):

- **`Assets: 172` but `showing 10 of 154`.** The frame's own asset count and the number of
  assets the listing returns disagree.
- **Rows with `None | None`.** Placeholder rows: assets that were registered but whose image
  upload never completed. They have no filename, no date, and never display on the frame.

## `sync` — make a frame match a directory

```
usage: pushframe sync [-h] [--frame FRAME] [--apply] [--yes]
                      [--delete | --hard-delete]
                      [--batch-size BATCH_SIZE] [--chunk-delay CHUNK_DELAY]
                      dir

positional arguments:
  dir            Local directory to scan for photos

options:
  --frame FRAME  Frame name (substring) or id
  --apply        Execute the plan (upload + delete) instead of only printing it
  --yes          Skip the confirmation prompt (required for --apply when running non-interactively)
  --delete       Remove gone-local photos from the frame instead of hiding them
                 (frame-scoped; the photo leaves this frame)
  --hard-delete  IRREVERSIBLY destroy gone-local photos instead of hiding them
                 (account-wide; requires typing the exact count to confirm)
  --batch-size BATCH_SIZE  Assets per select_asset/batch_update call (default 50)
  --chunk-delay CHUNK_DELAY  Seconds to pause between write chunks (default 5)
```

`sync` makes the frame **match the directory**. Photos in the directory but not on the frame
are uploaded; photos on the frame but not in the directory are *removed from view* — how, is
what `--delete` / `--hard-delete` control.

Matching is by **md5 content hash**, not filename, so renaming a file locally does not cause
a re-upload.

### Dry run is the default

Without `--apply`, nothing changes:

```bash
uv run pushframe sync ./photos --frame "Living Room"
```

```
Sync plan for Living Room (id: 00000000-...) — DRY RUN, nothing will be changed
To upload: 0
To hide: 96
To re-show: 0
Unchanged: 0
Already hidden: 3 (no action needed)
  - d3e4f5a6-4444-11f1-9666-0dddddddddd0 (taken 2026-07-09 06:47:55.775000)
58 frame assets without a content hash (e.g. videos) left untouched
```

(The removal line names the verb of the active mode — `To hide:` by
default, `To delete:` / `To hard-delete:` with the corresponding flag.)

Reading the plan:

| Line | Meaning |
|---|---|
| `To upload` | In the directory, not on the frame |
| `To hide` / `To delete` / `To hard-delete` | On the frame, no longer in the directory. **The verb tells you exactly which primitive will run.** |
| `To re-show` | On the frame but currently hidden, and back in the directory — it will be un-hidden, **not re-uploaded** |
| `Unchanged` | Present in both |
| `Already hidden` | Hidden and still absent locally — nothing to do |
| `...without a content hash` | Videos and placeholder rows. Never touched by sync. |

### The three removal modes

Photos no longer in the directory are **hidden by default**. The frame has no photo-count
limit, so preservation is the safe default: a mistaken sync should cost visibility, never
photos.

| Flag | Primitive | What happens | Reversible |
|---|---|---|---|
| *(none)* | `exclude_asset` | Hidden — stops displaying, stays in your account and on the frame | **Yes** — put the file back and re-run |
| `--delete` | `remove_asset` | Removed from *this frame*; the asset survives in the account | Partly — it must be re-uploaded |
| `--hard-delete` | `delete_asset` | **Destroyed account-wide** | **No** |

`--delete` and `--hard-delete` are mutually exclusive, enforced at parse time:

```bash
uv run pushframe sync ./photos --frame "Living Room" --delete --hard-delete
# pushframe sync: error: argument --hard-delete: not allowed with argument --delete
```

### Applying a plan

```bash
uv run pushframe sync ./photos --frame "Living Room" --apply
```

You get one confirmation covering the whole plan, echoing the resolved frame's name and id
so a loose `--frame` substring can't apply to the wrong frame:

```
About to apply this plan to "Living Room" (id: 00000000-...). Proceed? [y/N]
```

Then the summary — again naming the verb that actually ran:

```
Uploads: 0 succeeded, 0 failed
Hidden: 1 succeeded, 0 failed
Re-shown: 0 succeeded, 0 failed
```

Failures are named individually and make the run exit `1`:

```
Hidden: 1 succeeded, 1 failed
  ! e7f8a9b0-5555-7666-8777-0eeeeeeeeeee: Client error '401 Unauthorized' for url '...'
```

### Non-interactive runs

Without a TTY, `--apply` requires `--yes` and otherwise **fails closed** rather than hanging
on a prompt:

```bash
uv run pushframe sync ./photos --frame "Living Room" --apply < /dev/null
# --apply requires --yes when running non-interactively    (exit 1)

uv run pushframe sync ./photos --frame "Living Room" --apply --yes   # runs
```

### The `--hard-delete` gate

Because it is irreversible and account-wide, `--hard-delete` does **not** use the y/N prompt.
It requires re-typing the exact number of photos, so you have to read the count first:

```
To hard-delete: 1
IRREVERSIBLE: 1 photo(s) will be permanently destroyed account-wide, not just removed from
this frame. This cannot be undone.
To confirm, type the number of photos to hard-delete (1): y
Aborted.
```

Answering `y` — which would satisfy any ordinary prompt — aborts. Only the exact count
proceeds:

```
To confirm, type the number of photos to hard-delete (1): 1
Hard-deleted: 1 succeeded, 0 failed
```

`--yes` skips this gate like any other. **Be deliberate**: `sync --apply --yes --hard-delete`
destroys every frame photo missing from the directory, with no prompt.

### Restoring a hidden photo

Because hiding is reversible and hidden photos still count as present for deduplication, the
round trip is just moving the file:

```bash
mv ./photos/sunset.jpg /tmp/                                        # hide it
uv run pushframe sync ./photos --frame "Living Room" --apply --yes   # -> To hide: 1

mv /tmp/sunset.jpg ./photos/                                        # bring it back
uv run pushframe sync ./photos --frame "Living Room" --apply --yes   # -> To re-show: 1
```

The second run reports `To upload: 0` — the photo is re-shown, never uploaded a second time.

## `push` — upload only, never removes

```
usage: pushframe push [-h] --frame FRAME [--apply] [--yes] [--limit LIMIT]
                     [--batch-size BATCH_SIZE] [--chunk-delay CHUNK_DELAY]
                     [--max-wait MAX_WAIT] [--no-wait] [--country COUNTRY]
                     [--ignore-budget]
                     dir

positional arguments:
  dir                      Local directory of photos to upload (a supply/"buffet"; the
                           frame is NOT synced to match it)

options:
  --frame FRAME            Frame name (substring) or id
  --apply                  Execute the upload instead of only printing the plan
  --yes                    Skip the confirmation prompt (required for --apply when running
                           non-interactively)
  --limit LIMIT            Upload at most N photos this run
  --batch-size BATCH_SIZE  Assets per select_asset/batch_update call (default 50)
  --chunk-delay CHUNK_DELAY  Seconds to pause between write chunks (default 5)
  --max-wait MAX_WAIT      Max seconds to wait for write budget before stopping (default 3600)
  --no-wait                Stop immediately instead of waiting when the write budget is exhausted
  --country COUNTRY        Override the expected account country for the geo pre-flight guard
  --ignore-budget          Escape hatch: bypass the write budget entirely for this run
```

`push` is **structurally additive**: the removal list is forced empty, so it can never hide,
remove, or re-show anything. It is the safe way to add photos from a supply directory without
the frame being diffed to match it.

```bash
uv run pushframe push ./buffet --frame "Living Room"
```

```
Push plan for Living Room (id: 00000000-...) — additive (no deletes), DRY RUN, nothing will be changed
To upload: 4
To delete: 0 (additive mode — existing frame photos left untouched)
Unchanged: 0
  + ./buffet/probe_A.jpg
53 frame assets without a content hash (e.g. videos) left untouched
```

```bash
uv run pushframe push ./buffet --frame "Living Room" --apply --yes
```

```
Uploads: 4 succeeded, 0 failed
```

Photos already on the frame are skipped by md5, so re-running `push` on the same directory
uploads nothing.

### Pacing flags

These exist because the API has an anti-abuse layer that counts **requests**, not photos.
Batching matters far more than sleeping: at `--batch-size 50`, fifty photos cost about two
requests.

| Flag | Default | Use it when |
|---|---|---|
| `--limit N` | all | You want a controlled probe rather than the whole directory |
| `--batch-size N` | 50 | Rarely. Lowering it multiplies your request count. |
| `--chunk-delay S` | 5 | You want to spread a very large upload out further |

### Write budget and geo guard

`push` and `sync --apply` both run a client-side token-bucket budget and a geo pre-flight
check before any write, so the anti-abuse lockout is difficult to reach by accident.The budget is persisted between runs under `AURA_STATE_DIR` (default
`~/.config/pushframe/`), keyed by a hash of the **account identity**: the
`PUSHFRAME_EMAIL`/`AURA_EMAIL` override when set, otherwise the email of
the stored session (the same env-then-config resolution auth uses —
5.1.6). A run with no resolvable identity at all skips pacing with a
named stderr line instead of crashing.

| Flag | Effect |
|---|---|
| `--max-wait S` | Cap how long a run will wait for the budget to refill (default 3600) |
| `--no-wait` | Don't wait at all — stop as soon as the budget is dry |
| `--country XX` | Expected account country for the geo check (default `AURA_COUNTRY`) |
| `--ignore-budget` | Bypass the budget entirely. Escape hatch. |

When the budget runs dry:

```
Write budget exhausted, come back in ~40 min (or pass --no-wait / raise --max-wait).
```

When the exit IP country doesn't match:

```
VPN/exit IP in BE, account expects FR — switch your VPN and retry.
```

The geo check only runs if `AURA_COUNTRY` (or `--country`) is set; unset means skipped.

> These flags live on `push` only. `sync --apply` still gets the same budget and geo
> protection — it just takes its settings from the environment rather than per-run flags.

## `reconcile` — account for stuck placeholder rows

```
usage: pushframe reconcile [-h] --frame FRAME [--remove] [--yes]
                          [--mechanism {remove,hard-delete,complete}]
                          [--max-age-hours MAX_AGE_HOURS]
                          [--include-unknown-age]

options:
  --frame FRAME         Frame name (substring) or id
  --remove              Attempt removal of stuck placeholder rows instead of
                        only reporting them
  --yes                 Skip the confirmation prompt (required for --remove
                        when running non-interactively)
  --mechanism {remove,hard-delete,complete}
                        Which removal mechanism to attempt. 'remove' (the
                        default) is confirmed working live as of 2026-09-03
                        (plan 11-06); 'hard-delete' is unconfirmed; 'complete'
                        is not yet implemented
  --max-age-hours MAX_AGE_HOURS
                        Minimum age in hours for a placeholder row to be
                        reported as stuck rather than recently created
                        (default 24)
  --include-unknown-age
                        Explicit opt-in: treat rows whose creation time this
                        API never sends (unknown_age) as eligible for
                        removal too, not just rows old enough per
                        --max-age-hours. Bare --remove (this flag omitted)
                        cannot touch unknown-age rows.
```

`reconcile` is **data hygiene on existing frame state**, deliberately separate from `sync`/
`push`: it accounts for the placeholder rows described in
[Known issues](#known-issues) — rows a failed or abandoned upload left behind, with no
filename, no upload date and no content hash.

A placeholder is identified narrowly: `uploaded_at`, `file_name` and `md5_hash` must **all**
be null. A video (hashless by design, but it does have a filename and an upload date) or a
row still mid-upload-processing trips at most one of the three and is never counted.

### Report (the default)

```bash
uv run pushframe reconcile --frame "Living Room"
```

```
Frame: Living Room (id: 00000000-...)
Assets scanned: 172
Placeholder rows: 58
  stuck (older than 24.0h): 58
  recently created (may still be processing): 0
  creation time unknown: 0
    - e7f8a9b0-5555-7666-8777-0eeeeeeeeeee
    - ...
```

Rows younger than `--max-age-hours` (24h default) are reported separately as **recently
created** and are never treated as removable — a row created seconds ago by a legitimate
in-progress upload has the exact same null shape as a genuinely stuck one.

A row whose creation time cannot be determined at all is, **by default**, treated the same
way as a too-young row: reported, never removable. This is `inspect`'s single
placeholder-count line in detail, computed by the exact same function so the two numbers can
never disagree.

**Why this matters more than it sounds (`--include-unknown-age`, plan 11-06).**
`/frames/{id}/assets.json` never sends a `created_at` key on this account at all (confirmed
live 2026-09-03, against the raw JSON response, not just the parsed model) — so every
placeholder row's creation time is unresolvable, unconditionally. Left unconditional, the
"unknown age" guard doesn't just fail *conservatively* toward not deleting; it makes the
`stuck` bucket permanently empty and the removal path permanently unreachable, no matter how
old a row genuinely is or what `--max-age-hours` is set to. `--include-unknown-age` is the
deliberate, explicitly-named way to widen eligibility past that dead end: pass it alongside
`--remove` to treat unresolvable-age rows as `stuck` too. Omitting it keeps every prior
guarantee — no row becomes eligible by accident, and the reported counts without the flag are
identical to before it existed.

Without `--remove`, `reconcile` performs **no write of any kind** — it only reads.

An empty asset listing is refused with a named error rather than reported as zero
placeholders, since the two are indistinguishable from an API response alone.

### `--remove`

`--remove` is required to attempt any write; without it, `reconcile` cannot mutate anything.

**`--mechanism remove` (the default) is confirmed working, live, 2026-09-03 (plan 11-06).**
Against 3 rows promoted from `unknown_age` to `stuck` via `--include-unknown-age` (see above),
`remove_asset` returned `HTTP 200 {"number_failed":0}` and a follow-up read confirmed all 3
rows genuinely gone — not just acknowledged. This supersedes the earlier Phase 10 UAT finding
that `remove_asset` returned "not found": that earlier probe never had a genuinely
`stuck`-classified row to send, because the age guard's pre-11-06 unconditional form made
`result.stuck` permanently empty against this API (see [Known issues](#known-issues)).
`--mechanism hard-delete` remains unconfirmed — the probe never needed it, since `remove`
cleared every targeted row. `--mechanism complete` — reserved for treating a stuck row as an
unfinished upload to *finish* (filling in real `file_name`/`md5_hash`/`uploaded_at`) rather
than a bad row to delete — is still not implemented and still raises; it was never needed
either, and REL-05 no longer depends on it.

Only the **stuck** bucket is ever a removal candidate — recently-created and unknown-age
rows (the latter unless promoted by `--include-unknown-age`) are structurally unreachable from
the removal code path, whatever `--mechanism` or confirmation you give.

```bash
uv run pushframe reconcile --frame "Living Room" --remove --include-unknown-age
```

On an account whose `created_at` is genuinely absent from the assets listing (as observed on
the operator's account, see [Known issues](#known-issues)), `--include-unknown-age` is
required to get any rows into the `stuck` bucket at all — otherwise `--remove` alone still has
nothing to act on, exactly as before this flag existed:

```
Assets scanned: 156
Placeholder rows: 50
  stuck (older than 24.0h): 50
  ...
About to attempt removal of 50 stuck row(s) on "Living Room" (id: 00000000-...) using mechanism "remove". Proceed? [y/N]
```

```
Removed: 50 succeeded, 0 failed
```

**Historical failure mode, pre-11-06:** before the age-guard opt-in existed, `--remove`
(without a way to reach `unknown_age` rows) had nothing eligible to send on this account, so
`remove_asset` was never re-probed against a genuinely `stuck` row here after Phase 10. The
Phase 10 UAT session that first probed it got `404 Not Found` — a different result from the
`200 {"number_failed":0}` / confirmed-removed outcome above, from a different asset id shape.
Neither result should be assumed to generalize to every account; `reconcile --remove` reports
each attempt's real outcome rather than assuming either history.

`--mechanism hard-delete` uses the same escalated, exact-count-typing confirmation
`sync --hard-delete` does — irreversible, account-wide, no y/N shortcut — since a wrongly-run
hard-delete destroys real photos, not placeholder rows. It remains unconfirmed on this
account, since `--mechanism remove` (the default) already worked.

Without a TTY, `--remove` requires `--yes` and fails closed exactly like `sync`/`push --apply`.

**Candidate cap.** A single `--remove` run refuses to act on more than 25 stuck rows at
once, regardless of which mechanism you pass or whether it is already confirmed working — a
bounded, time-boxed probe rather than a bulk operation that could either burn a large write
budget on a mechanism that turns out not to work on some other account, or (if a mechanism is
more destructive than expected there) act on every stuck row on the frame in one run.
`reconcile` reports this refusal as a named error rather than silently truncating the
candidate list.

`--remove` draws from the same account-wide write budget as `sync --apply`/`push --apply`
(see [Write budget and geo guard](#write-budget-and-geo-guard)) and paces its requests the
same way.

## Google commands

Three verbs mirror a Google Photos album onto a frame — **the flagship
feature of pushframe** (one-time `google-link` → pick the album with
`google-album` → mirror it with `google-sync`, schedulable nightly per
[pair](#pairs--one-album--several-frames-and-back)). The full narrative
(with a verified end-to-end walkthrough, cache/manifest internals and
SAFE-01..04 semantics) lives in the
[README highlight section](../README.md#highlight-mirror-a-google-photos-album-onto-your-frame);
this is the command-level reference.

### `google-link` — link (or re-link) Google Photos

One command for both linking and re-linking: opens a **visible** Chrome
window (anti-bot posture — never headless) on a dedicated profile, waits
for the login (auto-detect on auth cookies, 30 min timeout), then
harvests the session cookies into the vault:

```
~/.config/pushframe/google-cookies.json   (0600, outside the repo)
```

The window closes by itself as soon as the login is detected — that is
the auto-detect working, not a crash. On a headless server, connect with
`ssh -X` so the window can open on your screen; the vault survives
logouts and every other command is headless by nature. Profile
precedence: `PUSHFRAME_PROBE_CHROME_PROFILE` > legacy
`AURA_PROBE_CHROME_PROFILE` > built-in default (created on demand).

### `google-album` — inspect a shared album

```
usage: pushframe google-album [-h] [--list] [target]
```

`--list` lists the account's shared albums (discovery aid). With a
`target` (share URL, `AF1Qip…` id, or album-name substring — ambiguity
prints a numbered list and exits 2), enumerates every item with its exact
disk weight. Read-only.

### `google-sync` — mirror an album onto a frame

```
usage: pushframe google-sync [-h] [--frame FRAME] [--all] [--pair PAIR]
                             [--scheduled] [--apply] [--yes] [--debug]
                             [--batch-size BATCH_SIZE]
                             album
```

Dry-run by default. `--apply` runs one y/N (echoing the resolved frame's
name and id) then mirrors: uploads, and removals as **hides only** —
this verb has no delete tier (SAFE-03). An empty or truncated album
listing aborts instead of planning (SAFE-01); a plan whose removals
exceed the mass-hide threshold needs an explicit confirmation (SAFE-02;
skip-and-log instead when `--scheduled`); videos are counted and skipped,
never silently dropped. Prerequisites: the cookie vault (`google-link`,
checked named before anything else) and an Aura session (same one-session
path as every verb).

Frame targeting: `--frame "Name"` for one album→frame pair, `--pair NAME`
for a named pair from the config, or `--all` for every configured pair in
sorted order with one shared write budget (a failing pair never blocks
the others; exit 1 if any failed). Exit codes: `0` dry-run/aborted
confirmation, `1` failure, `2` usage/ambiguity (album not found,
no pairs configured…).

A real steady-state run prints `Applied: N uploaded, K hidden, R re-shown`
(e.g. `Applied: 74 uploaded, 2 hidden, 0 re-shown` in 1:26 on the live
95-photo production run), then prunes the staging cache — only
manifest-backed progress survives.

## Choosing between `sync` and `push`

|  | `sync` | `push` |
|---|---|---|
| Uploads new photos | Yes | Yes |
| Can remove/hide photos | **Yes** | **Never** |
| Frame ends up matching the directory | Yes | No |
| Use for | A directory that *is* the intended frame contents | A supply directory you're adding from |

If you are not sure, use `push`. It cannot take anything away.

## Environment variables

Every setting resolves **at use time** with the precedence **environment →
config file → default**; environment variables therefore override anything
stored by `pushframe config` (and `config show` tells you when that happens).
A `.env` file at the project root is loaded automatically for dev runs; `.env`
is gitignored — never commit real credentials. Prefer `pushframe config import`
to adopt one.

**Required**

| Variable | Purpose |
|---|---|
| `PUSHFRAME_EMAIL` (legacy `AURA_EMAIL`) | Account email |
| `PUSHFRAME_PASSWORD` (legacy `AURA_PASSWORD`) | Account password (plaintext) |

**Optional — write budget and geo guard**

| Variable | Default | Purpose |
|---|---|---|
| `AURA_COUNTRY` | *(unset)* | Expected account country for the geo pre-flight check. Unset disables the check. |
| `AURA_GEO_FAIL_OPEN` | `true` | If the country lookup fails, continue rather than block |
| `AURA_WRITE_BUDGET_CAPACITY` | `30` | Token-bucket capacity, in requests |
| `AURA_WRITE_BUDGET_REFILL_PER_MIN` | `0.75` | Refill rate per minute |
| `AURA_WRITE_BUDGET_WAIT` | `true` | Wait for refill instead of stopping |
| `AURA_WRITE_BUDGET_MAX_WAIT` | `3600` | Max seconds to wait |
| `AURA_STATE_DIR` | `~/.config/pushframe` | Where the persisted budget lives |

**Optional — client identity**

| Variable | Default |
|---|---|
| `AURA_LOCALE` | `en-US` |
| `AURA_APP_IDENTIFIER` | `com.pushd.client` |
| `AURA_DEVICE_IDENTIFIER` | `0000000000000000` |
| `AURA_USER_AGENT` | `Aura/4.7.4271 (Android 36; Client)` |

The client-identity defaults are the ones a fresh install ships with —
`config set DEVICE_IDENTIFIER "$(uuidgen)"` (and optionally `USER_AGENT`)
overwrites them in the config file, which is the documented posture above.

**Optional — Google-side**

| Variable | Purpose |
|---|---|
| `PUSHFRAME_PROBE_CHROME_PROFILE` | Dedicated Chrome profile dir for `google-link` (precedence over the legacy `AURA_PROBE_CHROME_PROFILE`, then the built-in default created on demand) |
| `PUSHFRAME_GOOGLE_SYNC_REMOVAL_THRESHOLD` | SAFE-02 mass-hide threshold for `google-sync` (fraction of the frame's photos; see README) |
| `PUSHFRAME_VAULT_PATH` | Override the Google cookie vault path (mainly for tests) |

Booleans accept `1`, `true`, `yes`, `on` (case-insensitive); anything else is false.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success — including a dry run, and including an aborted confirmation (nothing happened, which is not a failure) |
| `1` | Missing credentials, login failure, frame not found or ambiguous, any per-item upload/removal/re-show failure, rate-limit abort, geo mismatch, or exhausted budget |

A partially-failed apply exits `1` and names each failed item. Successful work already done
is still reported.

## Known issues

### Writes intermittently fail with HTTP 401, and succeed on retry

Roughly 4 in 10 live write runs have been observed failing with a `401 Unauthorized` and
succeeding on an immediate re-run, with no change to credentials, config, or network. It
affects uploads, hides and deletes alike.

**There is no automatic retry yet.** If an apply reports 401 failures, simply run it again.
The operation is safe to repeat: uploads dedupe by md5, and hides are idempotent.

This covers the *transient* 401s — the no-special-body failures. If the
body says `"Request Unauthenticated"`, that is the anti-abuse trip, not
a transient failure: do NOT run it again, do NOT re-login — see [When
the anti-abuse layer refuses your
writes](#when-the-anti-abuse-layer-refuses-your-writes).

### A frame's asset count disagrees with its listing

`inspect` can report e.g. `Assets: 172` while listing `154`. The frame's own counter and the
asset listing disagree server-side. The listing is the number sync acts on.

### Placeholder rows accumulate

An asset registered by a failed or abandoned upload leaves a row with no image, no filename
and no hash — visible in `inspect` as `None | None`. They never display on the frame and sync
ignores them. They also appear to be the cause of the count mismatch above.

Run `pushframe reconcile --frame ...` to see exactly how many a frame has, split into stuck /
recently-created / unknown-age. **`--remove --mechanism remove` (the default) is a confirmed
working removal mechanism as of 2026-09-03 (plan 11-06)** — see the next paragraph and
[`reconcile`](#reconcile--account-for-stuck-placeholder-rows) for the command and evidence.
On an account where the assets endpoint never sends `created_at` (see below), reaching any
row to remove requires the explicit `--include-unknown-age` opt-in.

**Live-verified 2026-09-03 (plan 11-05):** on the account tested, the `stuck` bucket was, at
the time, unreachable. `/frames/{id}/assets.json` never sends a `created_at` key at all
(confirmed against the raw JSON response, not just the parsed model) — every placeholder row
therefore resolved to `unknown_age`, never `stuck`, regardless of `--max-age-hours`, because
the age guard treated an unresolvable creation time exactly like a too-young row by design
(fail toward not deleting). That live run counted **53 placeholder rows across 157 scanned
assets, all in `unknown_age`** — a different count from the 58 stuck rows recorded when this
issue was first found (2026-08-25); the delta was not explained and neither number should be
assumed current. Because there were zero `stuck` candidates that day, no removal mechanism was
attempted live — the `remove`/`hard-delete` results referenced then were Phase 10's historical
findings, not freshly reconfirmed. Widening what counts as an eligible candidate (an explicit,
opt-in `unknown_age` policy) was considered and deliberately deferred to a follow-up plan
rather than decided inside 11-05.

**Live-verified 2026-09-03 (plan 11-06), same day, later session:** the age guard's
unconditional form was corrected — `find_placeholders` gained the `unknown_age_policy`
opt-in (`--include-unknown-age` on the CLI) that promotes an unresolvable-creation-time row
to `stuck` instead of parking it, with the default left byte-for-byte unchanged. Through that
corrected gate, a time-boxed probe targeted 3 rows (the hard cap for a first live attempt) and
`--mechanism remove` (`FrameApi.remove_asset`) cleared **all 3**, confirmed by re-reading the
frame afterward (159 → 156 assets; the 3 targeted ids were genuinely absent, not merely
acknowledged with a 200). A clean follow-up `reconcile` report then showed 156 assets scanned,
50 placeholder rows, all still `unknown_age` by default (as expected — the default was
unaffected by this run). This supersedes the Phase 10 UAT finding that `remove_asset` 404d:
that earlier probe never had a genuinely `stuck` row to send, for the same structural reason
plan 11-05 diagnosed. `--mechanism hard-delete` and `--mechanism complete` were not needed and
remain unconfirmed/unbuilt respectively — `remove` cleared every targeted row on its own. Full
command transcript and raw HTTP evidence: `11-LIVE-FINDINGS.md`, "Plan 11-06" section.
