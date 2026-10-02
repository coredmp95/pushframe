# pushframe — a CLI for your Aura photo frame [unofficial]

[![tests](https://github.com/coredmp95/pushframe/actions/workflows/tests.yml/badge.svg)](https://github.com/coredmp95/pushframe/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/downloads/)

**pushframe is a command-line tool for your Aura digital photo frame. It can
keep your frame mirroring a Google Photos album — automatically, every night —
add photos from a folder on demand, and manage what's on the frame. All from
your computer, without the mobile app.**

Free software (MIT). Unofficial community tool built on reverse-engineered,
undocumented APIs — **not affiliated with or endorsed by Aura Frames Inc.**,
and the API can change without notice. Provenance: a renamed, heavily extended
fork of [zmanowar/auraframes](https://github.com/zmanowar/auraframes) — the
original 2023 reverse engineering is his work (see
[Credits](#credits) and [LICENSE](LICENSE)).

## The highlight: your frame follows a Google Photos album

Pick a Google Photos album (shared albums included) and a frame. From then on,
every run — manual or scheduled nightly — makes the frame **match the album**:

- **Add a photo to the album** → it appears on the frame at the next run.
- **Remove a photo from the album** → it stops displaying on the frame. It is
  **never deleted** — put it back in the album and it comes right back.
- **Nothing uploads twice.** Photos are recognized by their content, whether
  they were uploaded by pushframe or by the phone app, under any name.
- **A run with nothing new costs nothing**: it just lists the album and the
  frame — no downloads, no uploads. That makes scheduling it every night a
  non-event.

Set it up in three commands:

```bash
# 1. One-time: link your Google account. A browser window opens; log into
#    Google inside it, the rest is automatic (re-linking is the same command).
pushframe google-link

# 2. Find your album and preview what would happen (nothing is written yet):
pushframe google-album --list
pushframe google-sync family --frame "Living Room"

# 3. Mirror it (one confirmation, echoing the frame's name so you can check):
pushframe google-sync family --frame "Living Room" --apply
```

Then let it run itself:

```bash
# Name the album↔frame mapping once...
pushframe config pair add family --album family --frame "Living Room"
# ...and mirror it every night, unattended:
pushframe schedule add nightly --pair family --every 1d
```

On a server or any headless machine, enable lingering once so the timer fires
without a logged-in session: `loginctl enable-linger $USER`. Only the one-time
`google-link` needs a screen — on a server, connect with `ssh -X` for that one
command; everything else is headless by nature.

→ Full walkthrough with real outputs: [Google Photos albums](#google-photos-albums--google-link--google-album)
and [`google-sync`](#google-sync--mirror-a-google-album-onto-a-frame) below.

## What you need

- An Aura account (email + password) and a paired frame.
- A Google account with the album you want to mirror (for the Google feature).

That's all — the install channels below provide everything else: the deb/APT
packages need no Python (they carry their own runtime), and `uv tool install`
manages its own too. Only building from source assumes
[`uv`](https://docs.astral.sh/uv/) is present. For the Google feature,
`google-link` additionally needs a Chrome/Chromium browser the one time it
links your account — the command checks and names the exact remedy if
something is missing.

Release history lives in [CHANGELOG.md](CHANGELOG.md).

## Install

Every channel ships the same release; same CLI, same config
(`~/.config/pushframe/`), and `pushframe --version` tells you what runs.
**On Ubuntu/Debian, the APT repository is the simple path — nothing else to
install.**

### APT repository (Ubuntu/Debian — recommended)

A signed APT repository is served from this project's GitHub Pages. Three
commands, exactly as verified in a clean container — no Python and no other
runtime required (amd64):

```bash
# 0. the apt keyring dir (already present on recent systems)
sudo install -d -m 0755 /etc/apt/keyrings

# 1. trust the repository key
#    (dedicated signing key, fingerprint
#     0EE2DB2DB1360C58C4B2E0BF2EC06828F722F391)
curl -fsSL https://coredmp95.github.io/pushframe/dists/pushframe.asc \
  | sudo gpg --dearmor -o /etc/apt/keyrings/pushframe.gpg

# 2. add the sources entry
#    (arch=amd64: the repo is amd64-only — on multi-arch machines this
#     silences apt's i386 notice)
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/pushframe.gpg] https://coredmp95.github.io/pushframe stable main" \
  | sudo tee /etc/apt/sources.list.d/pushframe.list

# 3. install
sudo apt update && sudo apt install pushframe
```

`apt update` must stay free of signature warnings — if it is not, compare the
key fingerprint above with `gpg --show-keys /etc/apt/keyrings/pushframe.gpg`.
Future updates arrive with the usual `sudo apt update && sudo apt upgrade`.

### One-off .deb file

Pre-built `.deb` packages embed their own Python 3.14 runtime under
`/usr/lib/pushframe/` — **no system Python is used or modified**, and the
package needs only `ca-certificates` and `libc6`. Removal is clean (the
package owns every file it ships, including bytecode; nothing is written
into the system tree at run time).

```bash
sudo apt install ./pushframe_<version>_amd64.deb
```

### uv tool (or pipx) — any Linux distro, per-user

The PyPI package is pure Python and self-contained; `uv` (or `pipx`) manages
an isolated environment for the tool — no system Python is touched, and you
don't need Python installed at all:

```bash
uv tool install pushframe      # or: pipx install pushframe
pushframe --version
```

### From source (trying the latest master)

```bash
git clone https://github.com/coredmp95/pushframe && cd pushframe
uv sync
uv run pushframe --version
```

Everywhere in these docs commands are shown as plain `pushframe …`, which is
what the deb/APT and `uv tool install` installs put on your PATH. From a
source checkout, keep the `uv run` prefix (`uv run pushframe …`) so the
command runs inside the project's managed environment.

## Connect your account

The easy path is the wizard — it asks once, verifies the login against the
real API before writing anything, and stores the result in
`~/.config/pushframe/config.json` (mode `0600`):

```bash
pushframe config          # interactive wizard: email → password (hidden) → live login test
pushframe config show     # every setting: effective value (secrets ***), and where it comes from
pushframe config import .env   # adopt an existing .env without retyping it
pushframe config set KEY VALUE / get KEY / path
pushframe config pair add NAME --album ALBUM --frame FRAME   # name an album↔frame mapping (also: list, remove)
```

After the wizard, every command works with **no environment variables at all**.
Each value resolves at use time with the precedence **environment variable →
config file → built-in default**; `config show` warns when an env var shadows
what you put in the file. The wizard stores only your email plus the session
token — never the password.

<details>
<summary>Environment variables (override the config file)</summary>

**Required:**
- `PUSHFRAME_EMAIL`: The email of the account to authenticate with.
- `PUSHFRAME_PASSWORD`: The password of the account to authenticate with.

**Optional (with defaults):**
- `PUSHFRAME_LOCALE`: The locale of the device to mimic. (Default: `en-US`)
- `PUSHFRAME_APP_IDENTIFIER`: The identifier of the aura app. (Default: `com.pushd.client`)
- `PUSHFRAME_DEVICE_IDENTIFIER`: The unique identifier of the device to mimic. (Default: `0000000000000000`)
  - Set it to a unique value once (`pushframe config set DEVICE_IDENTIFIER "$(uuidgen)"`) — see [Troubleshooting](#troubleshooting).

**Optional — write budget & geo guard** (used by `sync --apply`, `push`, and `google-sync --apply`):
- `PUSHFRAME_COUNTRY`: Expected account country for the geo pre-flight check, e.g. `FR`.
  **Unset disables the check entirely.**
- `PUSHFRAME_GEO_FAIL_OPEN`: Continue if the country lookup itself fails. (Default: `true`)
- `PUSHFRAME_WRITE_BUDGET_CAPACITY`: Token-bucket capacity, in requests. (Default: `30`)
- `PUSHFRAME_WRITE_BUDGET_REFILL_PER_MIN`: Refill rate per minute. (Default: `0.75`)
- `PUSHFRAME_WRITE_BUDGET_WAIT`: Wait for a refill rather than stopping. (Default: `true`)
- `PUSHFRAME_WRITE_BUDGET_MAX_WAIT`: Max seconds to wait. (Default: `3600`)
- `PUSHFRAME_STATE_DIR`: Where the persisted budget lives. (Default: `~/.config/pushframe`)

**Optional — Google sync:**
- `PUSHFRAME_PROBE_CHROME_PROFILE`: Overrides the dedicated Chrome profile directory
  `google-link` uses for the one-time cookie harvest (default
  `~/.config/pushframe/chrome-profile`, created on demand).
- `PUSHFRAME_GOOGLE_SYNC_REMOVAL_THRESHOLD`: Fraction of the frame's photos above which
  `google-sync` demands explicit confirmation before hiding (Default: `0.2`).

Boolean variables accept `1`, `true`, `yes`, `on` (case-insensitive); anything else is false.

> **Legacy names:** the old `AURA_*` spellings (`AURA_EMAIL`, `AURA_STATE_DIR`,
> `AURA_WRITE_BUDGET_*`, `AURA_PROBE_CHROME_PROFILE`, …) are still read as
> fallbacks — one release of grace. The old config directory
> `~/.config/auraframes/` is migrated automatically to `~/.config/pushframe/`
> on the first run (the old directory is left untouched).
</details>

## Everyday use

A CLI wraps the library (installed as the `pushframe` entry point). There are
twelve commands — the Google trio first (the flagship flow, see the
[highlight](#the-highlight-your-frame-follows-a-google-photos-album) above),
then the rest alphabetically:

| Command | What it does | Writes? |
|---------|--------------|---------|
| `google-link` | Link (or re-link) your Google Photos account — one-time browser harvest | Vault write only (outside the repo) |
| `google-album` | Select a Google Photos album and enumerate it exactly | No (read-only) |
| `google-sync` | **Mirror a Google Photos album onto a frame** (dry run by default) | Yes, with `--apply` |
| `config` | Store credentials, pairs and settings once (wizard, `0600`) | Config file only |
| `doctor` | One deliberate write probe: can THIS machine write TODAY? | One 4×4 test image |
| `inspect` | Show one frame's photos and metadata | No |
| `logout` | Delete the stored session token — email and settings stay | Config only |
| `push` | Upload from a supply directory — **never** removes | Yes, with `--apply` |
| `reconcile` | Report (and optionally remove) stuck placeholder rows on a frame | Only with `--apply` |
| `schedule` | Install/list/remove systemd USER timers that run a Google album↔frame mirror unattended (`--pair`; `--sync-dir` for a local sync) | systemd units |
| `status` | Check credentials, log in, list your frames, show the Google link state | No |
| `sync` | Make a frame **match** a local directory | Yes, with `--apply` |

**→ Full reference with every flag, real output, and known issues: [`docs/CLI.md`](docs/CLI.md)**

### Check the connection (`status`)

```bash
pushframe status
```

```
PUSHFRAME_EMAIL: set
PUSHFRAME_PASSWORD: set
Logged in as you@example.com
1 frames:
  - Living Room (id: 00000000-0000-0000-0000-000000000000)
```

`--frame` (on every frame-targeting command) takes a **case-insensitive
substring of the frame name**, or an exact id. An ambiguous substring stops
the run and lists the matches rather than guessing:

```bash
pushframe inspect --frame "living"
```

### Google Photos albums — `google-link` / `google-album`

These commands read your **Google Photos** shared albums (a separate account
from the Aura API) — together with `google-sync` below they form the flagship
mirror flow, summarized in the
[highlight section](#the-highlight-your-frame-follows-a-google-photos-album).
The first command, `google-link`, opens a **visible** Chrome window on a
dedicated profile (default `~/.config/pushframe/chrome-profile`, created on
demand — your daily-driver profile is structurally unreachable); you log into
Google inside it, the command auto-detects the completed login, closes the
window, and saves the session to `~/.config/pushframe/google-cookies.json`
(`0600`, outside the repository). Every later operation is plain authenticated
HTTP — no browser runs again. **Re-linking is the same command**: when the
session expires (it does, eventually), run `google-link` again.

Then select and enumerate an album by name, share link, or id:

```bash
# Discover the account's shared albums:
pushframe google-album --list

# By name substring — ambiguity prints a numbered list and stops (exit 2):
pushframe google-album "holidays"

# By share link or album id — used exactly as given:
pushframe google-album "https://photos.google.com/share/AF1Qip...?key=..."
```

The resolved album is walked **completely** and every item's exact byte size
is measured, so the summary prints the exact disk weight without downloading
a single photo:

```
Album: Holidays 2026 (id shape: photos.google.com/share/AF1Qip…0001)
Items: 24 (pages: 1, exhausted: cleanly)
Disk weight: 90,813,552 bytes = 86.6 MiB (min 512,331, max 8,120,444, avg 3,783,898)
Per-item (index | id shape | WxH | bytes):
     1 | AF1Qip…base1 | 4898x3265 | 3,412,350
     ...
```

**Privacy posture:** session cookies live only in the `0600` vault outside the
repository; album capability URLs are secrets-like and are always printed
redacted (`AF1Qip…<last4>`). `status` reports the Google link state —
`linked: yes/no`, the account email, session usability — and never a cookie
value or token.

### `google-sync` — mirror a Google album onto a frame

One verb ties the Google side to the frame: enumerate the album, download what
is missing, upload it, and mirror removals as **hides**. Start to finish:

```bash
# One-time setup (or again whenever the Google session expires):
pushframe google-link

# Find the album, then mirror it:
pushframe google-album --list
pushframe google-sync family --frame "Living Room"           # dry-run plan (writes nothing)
pushframe google-sync family --frame "Living Room" --apply   # one y/N, then it mirrors

# Non-interactive (CI, scripts) — --yes is required for --apply, otherwise it fails closed:
pushframe google-sync family --frame "Living Room" --apply --yes
```

A real **production run** (a 95-photo album, 74 of them missing from the frame
and 2 removed since the last sync):

```
Plan: 74 to upload, 0 to re-show, 21 unchanged, 2 to hide, 2 already hidden
Videos skipped: 0 (metadata delta — videos are out of sync scope, never silently dropped)
Upload candidates: 74 items …
```

```text
Applied: 74 uploaded, 2 hidden, 0 re-shown in 1:26   ·   cache pruned, 95 files, 0 failures
```

After an apply the staging cache is pruned and a small manifest persists — so
a steady-state run costs one album listing and one frame listing, and its
`--apply` **downloads and uploads nothing**: the plan is all `unchanged` /
`already hidden`, with zero counts everywhere else. And when the plan has
nothing to do at all, the run simply reports it and exits — no confirmation,
no writes:

```text
Nothing to do — the frame already mirrors the album (757 unchanged, 8 already hidden).
```

#### Mirror semantics

- **Remove a photo from the Google album**, re-run with `--apply`: it is
  **hidden** on the frame — it stops displaying but stays. Re-add it to the
  album and the next run re-shows it **without re-uploading a byte**.
  (Both directions were proven live against a real album.)
- **Videos are skipped with a counted line** — never silently dropped.
- An **empty or truncated album listing aborts** with an error instead of
  producing a plan — a Google-side glitch can never read as "delete/hide
  everything".
- If a plan's removals exceed **20 % of the frame's photos**, an explicit
  confirmation echoes both counts first (threshold overridable via
  `PUSHFRAME_GOOGLE_SYNC_REMOVAL_THRESHOLD`).
- **This verb never deletes.** Removal means hide; the gated
  `--delete`/`--hard-delete` tiers stay with `sync` only.

#### One album, several frames, every night

Named album↔frame mappings (pairs) let one album feed several frames — and
feed the [scheduler](#scheduling--systemd-user-timers-no-root):

```bash
pushframe config pair add family --album family --frame "Living Room"
pushframe config pair list          # name → album → frame (+ state paths)
pushframe config pair remove family

pushframe google-sync "Album X" --pair family --apply --yes   # run one pair
pushframe google-sync "Album X" --all --apply --yes           # run every pair
```

`--all` runs every pair in sorted-name order; a failing pair is reported and
never blocks the others (exit 1 if any pair failed). Mirror state is **per
pair** — two pairs never share dedupe memory.

#### Headless servers (google-link without a screen)

The one command that needs a visible browser is `google-link` (anti-bot
posture: Chrome runs visible, never headless). On a server, connect with
X11 forwarding — `ssh -X user@host` (needs `X11Forwarding yes` and
`xauth` on the server) — then run `pushframe google-link`: Chrome opens
on YOUR screen while executing on the server. The cookie vault it writes
survives logout, and every other command is headless by nature — the display
is needed once, at link time.

### `push` — upload from a folder, never removes

`push` is structurally additive: it cannot hide, remove, or re-show anything.
Use it when you want to *add* from a folder without the frame being diffed to
match it.

```bash
# Dry run, then apply. Photos already on the frame are skipped by content hash.
pushframe push ./buffet/ --frame "Living Room"
pushframe push ./buffet/ --frame "Living Room" --apply --yes

# Pacing flags for the write budget:
#   --limit N         upload at most N photos this run
#   --batch-size N    assets per write call (default 50)
#   --chunk-delay S   seconds to pause between write chunks (default 5)
pushframe push ./buffet/ --frame "Living Room" --apply --yes --limit 40

# Budget / geo overrides:
#   --max-wait S      cap the wait for budget refill (default 3600)
#   --no-wait         stop instead of waiting when the budget is dry
#   --country XX      expected account country for the geo pre-flight guard
#   --ignore-budget   bypass the budget entirely (escape hatch)
```

**If you are unsure which to use, use `push`** — it cannot take anything away.

### `sync` — make a frame match a folder

`sync` makes the frame **match the directory**: photos in the directory but not
on the frame are uploaded; photos on the frame but no longer in the directory
are **hidden by default** (reversible — see below). Nothing changes without
`--apply`:

```bash
pushframe sync ./photos/ --frame "Living Room"
```

```
Sync plan for Living Room (id: 00000000-...) — DRY RUN, nothing will be changed
To upload: 12
To hide: 3
To re-show: 1
Unchanged: 84
Already hidden: 2 (no action needed)
```

Photos are matched by **content hash**, not filename — renaming a file locally
does not cause a re-upload.

```bash
# Apply it. One confirmation covers the whole plan and echoes the frame name + id.
pushframe sync ./photos/ --frame "Living Room" --apply

# Non-interactive (CI, scripts) — --yes is required, otherwise it fails closed.
pushframe sync ./photos/ --frame "Living Room" --apply --yes
```

### What happens to removed photos — hidden, not deleted

A photo that leaves your directory (or the Google album) is **hidden** by
default: it stops displaying but stays in your account, and comes straight
back when you restore it. The frame has no photo-count limit, so preservation
is the safe default — a mistaken sync should cost visibility, never photos.

| Flag (on `sync`) | Effect | Reversible |
|------|--------|------------|
| *(none)* | Hidden — stops displaying, stays on the frame | **Yes** |
| `--delete` | Removed from this frame | Must re-upload |
| `--hard-delete` | Destroyed account-wide | **No** |

`--hard-delete` does **not** accept a y/N answer. It makes you re-type the
exact count, so you have to read the number first:

```
To hard-delete: 12
IRREVERSIBLE: 12 photo(s) will be permanently destroyed account-wide, not just removed from
this frame. This cannot be undone.
To confirm, type the number of photos to hard-delete (12): y
Aborted.
```

Only `12` proceeds. Note that `--yes` skips this gate like any other, so
`sync --apply --yes --hard-delete` destroys without prompting — use it
deliberately.

Restoring a hidden photo is just moving the file back and re-running:

```bash
mv /tmp/sunset.jpg ./photos/ && pushframe sync ./photos/ --frame "Living Room" --apply --yes
# -> To re-show: 1   (and "To upload: 0" -- it is un-hidden, not uploaded again)
```

### Scheduling — systemd USER timers (no root)

```bash
pushframe schedule add nightly --pair family --every 1d
pushframe schedule list
pushframe schedule remove nightly
```

The timer runs a `google-sync` **of the Google album onto the frame** at every
tick (`--pair` resolves the named pair; `--sync-dir DIR --frame F` schedules a
local-directory `sync` instead; `--at "OnCalendar"` allows exact times like
`"Mon *-*-* 02:00"`). Units live under `~/.config/systemd/user/` — no root.
Everything comes from stored config (session, pair), so a timed run **never
prompts**. On failure the unit just ends and the next tick is the retry; logs
land in `~/.local/state/pushframe/<job>.log`. In scheduled mode the mass-hide
check **skips and logs** instead of proceeding — review manually. Headless
hosts need lingering enabled once: `loginctl enable-linger $USER`.

## Safety model — a mistaken run should never cost photos

- **Hide, don't delete.** Removals are hidden by default everywhere; the
  irreversible tiers are gated behind explicit flags and an exact-count
  re-typing confirmation.
- **Dry run is the default.** Every write verb shows its plan first; `--apply`
  plus one confirmation (echoing the resolved frame's name and id) does the
  work.
- **A plan hiding more than 20 % of a frame's photos stops and asks** —
  or skips and logs when scheduled.
- **Every write is paced** by a client-side budget (token bucket + geo
  guard), so the server's anti-abuse lockout is hard to reach by accident.
- **Runs are resumable.** Only confirmed writes are remembered — an
  interrupted run uploads the remainder exactly once, never twice.
- **Not sure this machine can write today?** `pushframe doctor` sends one
  4×4 test image through the real write path, verifies it arrived, cleans it
  up, and prints GO / NO-GO with the reason.

## Troubleshooting

**Writes are refused (401) while reads work.** The service's anti-abuse layer
can refuse an account's writes while reads keep answering. If an apply is
refused: **stop, wait 60+ minutes from the stop, then probe once** with
`pushframe doctor`. Still refused after hours? Switch to days: 24 h+ of total
silence (no command at all — automated probing keeps the lock alive), then ONE
read (`pushframe status`), and only then one `doctor`. Never re-login in a
loop and never retry refused writes — every attempt restarts the clock. Also
give the install its own client identity once:
`pushframe config set DEVICE_IDENTIFIER "$(uuidgen)"` (the shared all-zeros
default reads as "not a phone"). A refused write is not lost work: the run
stopped safely, and re-running later resumes exactly where it stopped.

**A write fails with 401 but an immediate re-run works.** A different, benign
flavor: the service occasionally 401s a write that succeeds on the next
attempt. Re-running is always safe — plans dedupe by content hash and hides
are idempotent.

**Session expired / a command says to run the wizard.** `pushframe config` —
Enter keeps your stored email, a fresh login refreshes the token.

**`google-link` can't open a browser (server).** Connect with `ssh -X` for
that one command — see [Headless servers](#headless-servers-google-link-without-a-screen).

**Something looks wrong on the frame (count mismatch, rows with `None`).**
`pushframe inspect` and `pushframe reconcile` report them; known server-side
quirks are catalogued in [`docs/CLI.md`](docs/CLI.md#known-issues).

**Any message you don't understand.** Every error message is explained —
symptom, meaning, remedy — in [`docs/ERRORS.md`](docs/ERRORS.md).

**Logs & exit codes.** Add `--debug` **before** the subcommand for verbose
request/response logging on stderr (`pushframe --debug status`). Every run
also writes a full log to `logs/file_{timestamp}.log`. Exit codes: `0`
success (a dry run and an aborted confirmation both count); `1` failure
(bad credentials, unresolvable frame, per-item failure, budget exhausted);
`2` usage/ambiguity (e.g. an album name matching several albums).

## Documentation map

**For users:**
- [`docs/CLI.md`](docs/CLI.md) — the full command reference: every flag, real
  output, known issues.
- [`docs/ERRORS.md`](docs/ERRORS.md) — what every error message means and how
  to fix it.
- [`CHANGELOG.md`](CHANGELOG.md) — what changed in each release.

**For the curious and for contributors:**
- [`docs/INTERNALS.md`](docs/INTERNALS.md) — how it works: the
  reverse-engineered API, the mirror engine, the anti-abuse findings, and
  what remains unknown.
- [`docs/DEVELOPING.md`](docs/DEVELOPING.md) — contributing: project layout,
  conventions, working method, and how releases ship.
- [`VERIFICATION-REPORT.md`](VERIFICATION-REPORT.md) — what was verified
  live, step by step.

## Credits

This is an unofficial, reverse-engineered client.

- **Original author:** [zmanowar](https://github.com/zmanowar) (`zach@codehooker.com`) —
  created the original Aura/Pushd Python client in 2023
  ([zmanowar/auraframes](https://github.com/zmanowar/auraframes)), including the
  reverse-engineered API models, the auth/read/upload flows, and the first version of this
  README. All of the reverse-engineering insight this project builds on is his work; his
  commits are preserved in this repository's git history.
- **Revive & extend (2026):** Fabrice DIDIERJEAN — modernized the ~3-year-old codebase onto
  Python 3.14 + `uv`, verified the read path live, then built and live-proved the whole
  current tool: the `pushframe` CLI (`status`/`inspect`/`sync`/`push`/`reconcile`), the
  batched anti-abuse write path, and the entire local Google Photos → frame mirror
  (cookie-vault linking, album enumeration, pruned-cache sync with a persistent
  manifest, hide-by-default semantics). Project renamed `pushframe` in v5.0.
