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
| `Unsupported image format: MPO …` on a few items (pre-5.1.9) | The file is a stereo/3D JPEG container (MPO) | Fixed in 5.1.9+: the first view uploads as plain JPEG. Update pushframe and re-run — the failed items retry automatically |
| `snAcKc carries a null inner payload on HTTP 200 …` | Google answered a page request with an empty envelope. One automatic retry already ran; if it recurs on every run it means the past-the-end request shape Google now answers with null (a listing-size change) — fixed in 5.1.9+; on older versions, update | Update pushframe; if it persists on the latest version, wait a few minutes and re-run |

## Writes, budget, pacing

| Message | What it means | What to do |
|---|---|---|
| A run stops mentioning consecutive failures / a lockout, and tells you what is confirmed | The anti-abuse breaker tripped mid-run | Wait 60+ minutes from the stop, then re-run once — it resumes where it stopped. Lowering `--batch-size` (e.g. 10) helps if volume is the trigger |
| The run pauses with a `pacing Ns` countdown | The client-side write budget is refilling — **normal** after the initial burst | Nothing; long waits are by design (interrupts are safe) |
| Budget exhausted / `--max-wait` reached | The configured wait ceiling was hit | Re-run later, or raise `PUSHFRAME_WRITE_BUDGET_MAX_WAIT` |
| A geo guard mismatch | The account country doesn't match the expected one | Set `PUSHFRAME_COUNTRY` (e.g. `FR`) or check the account |

## Scheduling

| Message | What it means | What to do |
|---|---|---|
| `schedule: no systemd USER session available … loginctl enable-linger $USER` | The user systemd session isn't available for timers | Run `loginctl enable-linger $USER` once (headless hosts), or log in once on the machine |
| `schedule: job name "…" must be alphanumeric/dashes` | The job name is used in unit filenames | Pick a name like `nightly` or `living-room-nightly` |
| A timer "fails" repeatedly | Read the job log | `~/.local/state/pushframe/<job>.log`; the unit ends on the first failure on purpose — the next tick is the retry |

## Anything else

A traceback you can read means a bug — please open an issue with the
`logs/file_*.log` content (secrets are redacted in messages; still, skim
before pasting). Start with `pushframe status` to confirm the account is
healthy, and `pushframe doctor` before assuming a write problem is yours.
