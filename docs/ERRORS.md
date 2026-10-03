# Understanding pushframe's error messages

Every failure is meant to tell you **what happened** and **what to do next**
— never a raw traceback. This page explains the messages you can meet, by
theme. Two universal reflexes first:

- **Add `--debug` before the subcommand** (`pushframe --debug status`) for
  full request/response logging on stderr, and note that every run writes a
  complete log to `logs/file_{timestamp}.log`.
- **Exit codes:** `0` = success (a dry run and an aborted confirmation both
  count as success); `1` = failure; `2` = usage or ambiguity (bad arguments,
  an album/frame name matching several things).

## Login, session, credentials

| Message | What it means | What to do |
|---|---|---|
| `not authenticated: …` / `Login failed: …` | The stored session is missing or the login was refused | Run `pushframe config` — Enter keeps your stored email; a fresh login refreshes the token |
| `SessionExpiredError` (scheduled runs) | A timed run's stored token expired | Run `pushframe config` once, interactively; the next tick will succeed. Timed runs never prompt by design |
| `pushframe config` says the wizard needs a terminal | You ran the interactive wizard from a script | Use `config set KEY VALUE` / `config import FILE` for non-interactive setup |

## Refused writes (the 401 family)

The service's anti-abuse layer can refuse an account's writes while reads
keep working. Two different flavors, and telling them apart matters:

- **A write fails but an immediate re-run works** (observed on roughly 4 in
  10 runs at some point). Benign: just re-run. Repeating is always safe —
  plans dedupe by content hash and hides are idempotent.
- **Refused writes persist** — `401 Unauthorized` with a
  `Request Unauthenticated` body on a *fresh* token, or an explicit lockout
  message on login. This is the anti-abuse "trip": it is account-wide, and
  **every call that touches it restarts a cooldown clock**. Do not re-login
  in a loop, do not retry. The full recovery protocol (60+ minutes, then the
  24-hour silence rule) is in the README's
  [Troubleshooting](../README.md#troubleshooting) and
  [docs/CLI.md](CLI.md#when-the-anti-abuse-layer-refuses-your-writes).

**Nothing is lost when a write run stops**: only confirmed writes are
remembered, and the next run uploads the remainder exactly once — never
twice.

## Frame and album resolution

| Message | What it means | What to do |
|---|---|---|
| Several candidates, a numbered list, exit `2` | Your `--frame` / album name is a substring matching several things; the tool refuses to guess | Re-run with a more specific substring, or pass the exact id |
| No match, exit `2` | Nothing matched | Check spelling with `pushframe status` (frames) or `pushframe google-album --list` (albums) |

## Google sync (`google-link` / `google-album` / `google-sync`)

| Message | What it means | What to do |
|---|---|---|
| `no Google session vault — run 'pushframe google-link'` | No saved Google session (first run, or it expired) | Run `pushframe google-link` (a visible browser opens; see README for headless servers) |
| `google-link` complains about playwright or Chrome | The one-time browser prerequisites are missing | The message names the exact remedy (install playwright / Chrome/Chromium) |
| `album listing is EMPTY … refusing to plan` | The album listing came back empty — indistinguishable from a truncated one, so the mirror **refuses to act** rather than risk hiding everything | Re-run; if it persists, check the album in Google Photos (sharing enabled, not empty) |
| `album listing is NOT exhausted cleanly …` | Same safety gate, truncated case | Re-run — a Google-side glitch never becomes a mass-hide |
| `⚠ N of M photos … would be hidden — over the … mass-hide safety threshold` | The plan hides more than 20 % of the frame's photos, so it demands explicit confirmation | Read the counts; `y` only if that is really what you want |
| `SKIPPED (--scheduled): this plan would hide too many photos …` | Same gate in scheduled mode: the run **skips and logs** instead of proceeding | Run `google-sync` manually, review the plan, apply deliberately |
| `google-sync failed: reading frame assets failed: …` | The frame listing itself failed | Re-run; if persistent, `pushframe status` / `pushframe inspect` to check the account and frame |
| An argparse usage error on a command whose argument is literally `config` or `schedule` (e.g. `google-sync "config" --frame config`) (pre-5.1.25) | The `config` / `schedule` verb word was matched anywhere in the command line, truncating it at the frame name and leaving argparse a dangling option | Fixed in 5.1.25 — the verb is only matched at position one; re-run the same command |
| `Unsupported image format: MPO …` on a few items (pre-5.1.9) | The file is a stereo/3D JPEG container (MPO) | Fixed in 5.1.9+: the first view uploads as plain JPEG. Update pushframe and re-run — the failed items retry automatically |
| `snAcKc carries a null inner payload on HTTP 200 …` | Google answered a page request with an empty envelope. One automatic retry already ran; if it recurs on every run it means the past-the-end request shape Google now answers with null (a listing-size change) — fixed in 5.1.9+; on older versions, update | Update pushframe; if it persists on the latest version, wait a few minutes and re-run |

## Writes, budget, pacing

| Message | What it means | What to do |
|---|---|---|
| A run stops mentioning consecutive failures / a lockout, and tells you what is confirmed | The anti-abuse breaker tripped mid-run | Wait 60+ minutes from the stop, then re-run once — it resumes where it stopped. Lowering `--batch-size` (e.g. 10) helps if volume is the trigger |
| The run pauses with a `pacing Ns` countdown | The client-side write budget is refilling — **normal** after the initial burst | Nothing; long waits are by design (interrupts are safe) |
| Budget exhausted / `--max-wait` reached | The configured wait ceiling was hit | Re-run later, or raise `PUSHFRAME_WRITE_BUDGET_MAX_WAIT` |
| A geo guard mismatch | The account country doesn't match the expected one | Set `PUSHFRAME_COUNTRY` (e.g. `FR`) or check the account |

## Config & pairs

| Message | What it means | What to do |
|---|---|---|
| `pair add: the pair name is missing — …` / `--album needs a value` / `unknown option "…"` / `… given twice` / `--frame is missing` | The `config pair add` tail is malformed — a dangling flag, a typo (valid ones are listed), a duplicate, or a missing required half of the album→frame mapping. Fixed after the 2026-10-02 audit — these used to crash with a raw traceback, or worse: silently create a pair literally named `--album` | Read the message: it names the exact token; the usage line below shows the full shape |
| `pair add: pair "…" already exists …` | The name is taken (the message shows its album/frame) | `config pair remove <name>` first, or choose another name |
| `config set: the key "…" starts with "--" — that looks like a flag, not a key (set takes exactly KEY VALUE)` / the same on the value (`… looks like a flag, not a value …`) | A flag-shaped word reached `config set` — an option misplaced, or the VALUE forgotten. Fixed in 5.1.25 — the word used to be stored as a literal key or value without a word | Count the words: `config set` takes exactly `KEY VALUE`; `pushframe config set --help` lists every key, explained |
| `config set <key>: unexpected "…" — usage: pushframe config set <key> <value>` / `config get: unexpected "…" — usage: pushframe config get <key>` | A stray extra word — exit 2, nothing stored or shown. Fixed in 5.1.25 — extras used to be ignored silently | The command takes exactly KEY VALUE (get: exactly one KEY); drop the extra word |
| `config import: unexpected "…"` / `unknown option "…"` / `--file given twice` / `--file needs a value` | The `config import` tail is malformed — the same strict parser as `pair add` (exit 2, nothing imported). Fixed in 5.1.25 — a dangling `--file` used to fall back to importing `.env` in silence (you thought you imported your file), unknown tokens were ignored, and the first of two `--file` flags won | Read the message; the usage line shows the shape. Bare `pushframe config import` (no flags) still imports `.env` — only a malformed tail is refused |
| `pair remove: unexpected "…" — usage: pushframe config pair remove <name>` | A stray extra word after the pair name — exit 2, nothing removed. Fixed in 5.1.25 — extras were ignored silently | One name per command; `pushframe config pair list` shows what is configured |

## Email reports (`schedule report` / `--report`)

The transport — WHERE reports go — is configured once with
`pushframe schedule report`; WHICH runs email, and how much detail, is
chosen per job at install time with `schedule add … --report LEVEL`
(see Scheduling below).

| Message | What it means | What to do |
|---|---|---|
| `schedule report: unknown option "…" — known ones: --to, --smtp-host, …` / `… given twice` / `… needs a value` / `unexpected "…"` | The configurator tail is malformed — the same ONE strict parser as `schedule add` / `pair add` (exit 2, nothing stored). Fixed in 5.1.25 — `--to --test` used to store the literal `--test` as the recipient (and swallow the trial-send — discovered only when the first email failed), and `--to a --to b` let the second address win without a word | Read the message: it names the exact token, and the full usage reprints below it. `--help` / `-h` / `help` prints that usage from anywhere on the line (a value slot is consumed first, so `--to help` still means the literal address `help`) |
| `schedule report: --show / --test / --disable are exclusive` | Exactly one action per call | Re-run with a single action flag |

## Scheduling

| Message | What it means | What to do |
|---|---|---|
| `schedule: no systemd USER session available … loginctl enable-linger $USER` | The user systemd session isn't available for timers | Run `loginctl enable-linger $USER` once (headless hosts), or log in once on the machine |
| `schedule: job name "…" must be alphanumeric/dashes` | The job name is used in unit filenames | Pick a name like `nightly` or `living-room-nightly` |
| `schedule add: the job name is missing — …` | The job name must come right after `add`, before any `--flag` (`schedule add --pair X` puts the flag in the name slot) | `schedule add nightly --pair X --every 1d` — the usage line below the message shows the shape |
| `schedule add: --FLAG needs a value` / `unknown option "…"` / `… given twice` / `unexpected "…"` | The `schedule add` tail is malformed — a dangling flag, a typo in a flag name (they are listed), a duplicated flag, or a stray word | Read the message: it names the exact token and, for unknown options, lists the valid ones. Fixed in 5.1.22 — these used to crash with a raw traceback (pre-5.1.22) |
| ``schedule list: unexpected "…" — `pushframe schedule list` takes no arguments`` / `schedule remove: unexpected "…" — usage: pushframe schedule remove <job>` | A stray extra word after the single argument these verbs take — exit 2, nothing listed or removed. Fixed in 5.1.25 — extras were ignored silently | `schedule list` takes no arguments; `schedule remove` takes exactly one job name |
| A timer "fails" repeatedly | Read the job log | `~/.local/state/pushframe/<job>.log`; the unit ends on the first failure on purpose — the next tick is the retry |
| `=== run 2026-10-04T00:02:59+02:00 job=nightly pushframe=5.1.26 ===` opens a job log, `=== run end rc=0 elapsed=42s ===` closes it | Every scheduled run stamps its log with its identity (when, which job, which pushframe — 5.1.26) and its outcome (exit code + duration). A missing footer means the run died by exception: the traceback and systemd's `Failed` line carry that story | Read `<job>.log` one block per run; `rc=0` = healthy. No footer → the traceback above it and `journalctl --user -u pushframe-<job>` tell the rest |
| `  ~ <asset-id> (taken 2020-01-01) — re-show` / `  - <asset-id> (taken <date>)` lines under the plan | 5.1.26: a scheduled delta names WHICH frame assets it touches (the shape the interactive dry-run prints) — a delta is identifiable, not just countable | Check the ids against your expectations; `google-sync --pair <name>` without `--apply` shows the same plan interactively |
| `<job>.log.1` (and `.log.2` … `.log.28`) appearing next to a job log | 5.1.27: past 5 MiB the log slides one generation older at run end, the oldest falling off — the live file stays bounded, weeks of history stay readable. Generations beyond a lowered keep are swept | Nothing — that is the size bound working. `PUSHFRAME_JOB_LOG_KEEP` tunes the depth (a junk value falls back to the default; a rotation failure never fails a run) |
| A `--sync-dir` job that never ran, with `unrecognized arguments: --scheduled` in its log (pre-5.1.27) | The unit's ExecStart passed `--scheduled` before the `sync` parser accepted it — every tick died at argparse (exit 2) before any sync | Fixed in 5.1.27 — upgrade and the existing unit works as-is; re-add it (`schedule add --sync-dir …`) to also pick up `--report-tag` (named header + rotation) |
| `⚠ would FAIL to start: unrecognized arguments: --nope — re-add the job …` under a job line in `pushframe status` | The unit's ExecStart uses a flag the CURRENT binary's argparse refuses — the job dies on every tick before doing anything (hand-edited unit, or a unit installed by a mismatched pushframe version). 5.1.27 | Fix the unit or re-add the job; `pushframe --version` against the unit's flags tells you which way the drift goes |

## Anything else

A traceback you can read means a bug —
[open a bug report](https://github.com/coredmp95/pushframe/issues/new?template=bug_report.yml)
with `pushframe --version`, your OS, the exact command and the full output
(never paste passwords, tokens, cookies, or full album share links). Start
with `pushframe status` to confirm the account is healthy, and `pushframe
doctor` before assuming a write problem is yours. Questions that are not
bugs go to [Discussions](https://github.com/coredmp95/pushframe/discussions);
anything that looks like a security problem goes to
[private vulnerability reporting](https://github.com/coredmp95/pushframe/security/advisories/new),
never a public issue (see [SECURITY.md](https://github.com/coredmp95/pushframe/blob/master/SECURITY.md)).
