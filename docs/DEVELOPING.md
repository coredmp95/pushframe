# Developing pushframe

Everything a contributor needs: how to get a working environment, the
conventions the codebase follows, how to work and verify, and how a release
ships. For *using* pushframe, start at the [README](../README.md) — this page
assumes you want to change it.

## Project layout

```
main.py                  read-path demo (login → list → fetch → download)
pushframe/
  cli.py                 the argparse surface and command dispatch
  aura.py                the Aura cloud client (login, frames, assets, writes)
  client.py              HTTP transport (httpx, HTTP/2), rate-limit/trip handling
  aws/                   S3 upload + SQS clients
  api/                   endpoint wrappers (frames, accounts)
  sync.py                diff engine + execute_plan (shared by sync/push/google-sync)
  gsync.py               the Google→frame mirror (plan, gates, apply)
  google/                Google-side session: cookie vault, album walk, cache
  session.py             one session path: env → stored token → prompt
  config_store.py        config.json read/write (0600, atomic)
  pairs.py               named album↔frame mappings
  schedule.py            systemd USER timer install/remove
  preflight.py           pre-run checks (vault, env, geo)
  doctor.py              the one-image write probe
tests/                   offline suite (pytest, randomized order) + live tests
scripts/                 release, APT-repo and container journey helpers
docs/                    user docs (CLI.md, ERRORS.md) + this page
```

## Environment

[uv](https://docs.astral.sh/uv/) is used exclusively — no pip/venv/poetry.
Python 3.14 is pinned via `.python-version`; uv fetches the interpreter if
needed.

```bash
uv sync                  # runtime deps, creates .venv
uv sync --extra dev      # + pytest & friends
uv run pushframe --help  # the CLI from source
uv run pytest -m "not live"   # the full offline suite
```

Live tests (`pytest -m live`) hit the real API and need real credentials in a
`.env` — they never run in CI.

## How the codebase is organized

- **One session path** (`session.py`): env credentials override, else the
  stored token session from `config`, else one interactive prompt. Every verb
  authenticates through it; non-interactive runs fail named instead of
  prompting.
- **One diff engine** (`sync.py`): `sync` (directory), `push` (additive) and
  `google-sync` (album) all end in `execute_plan` — uploads, hides,
  re-shows, removals, progress callbacks, the write budget. Changes to write
  behavior happen once, there.
- **Safety gates are structural, not decorative**: dry-run is the argparse
  default (`--apply` to act), removals are hide-by-default, the mass-hide
  gate and the empty-listing refusal live in the plan builders, and
  `--hard-delete` re-typing gates cannot be satisfied by `--yes` alone being
  convenient — read `cli.py`'s confirm helpers before touching them.
- **Secrets**: cookie values and capability URLs are redacted on print
  (`pushframe/google/redaction.py`); the vault and manifest are `0600`
  outside the repository. Never print token material.

## Conventions

- **User-facing text is plain language.** No internal task codes, no
  development jargon in messages, `--help` or docs — users can't decode
  `SAFE-02`-style references. Documentation is split by reader:
  `README.md` + `docs/CLI.md` + `docs/ERRORS.md` are for users;
  `docs/INTERNALS.md` and this page are for contributors.
- **Offline tests are the contract.** The suite runs in randomized order
  (`pytest -q -m "not live"`); anything network-touching belongs behind a
  fake transport or a `live` mark. Tests pin important user-visible strings
  (confirmation prompts, error messages) — update them together.
- **Every user-visible fix ships with its test and a CHANGELOG entry**
  (Keep a Changelog format; the `[Unreleased]` section accumulates until
  the next release).
- **Branches & commits:** `master` is the integration branch; commits follow
  conventional prefixes (`fix:`, `feat:`, `docs:`, `ci:`…) with a body
  explaining the *why*.

## Working method

The project evolved by short, verified increments; keep it that way:

1. Reproduce/define the behavior first — a failing test or a captured
   transcript.
2. Fix at the layer that owns the problem (transport ≠ engine ≠ CLI surface).
3. Run the offline suite; add/adjust the tests that pin the behavior.
4. Docs follow code: if a flag, message or behavior changed, the user docs
   (README/CLI.md/ERRORS.md) change in the same commit.

## Releasing

A release is one tag shipped to three channels simultaneously —
[PyPI](https://pypi.org/project/pushframe/), a signed
[APT repository](https://coredmp95.github.io/pushframe/) (GitHub Pages), and
GitHub Releases with a built `.deb`:

1. Bump the version in `pyproject.toml`, `pushframe/__init__.py` and
   `tests/test_version.py`; move CHANGELOG's `[Unreleased]` to `[X.Y.Z]`
   with the date. `uv build` updates `uv.lock` (version line only).
2. Commit `chore(release): vX.Y.Z — <title>`, tag annotated, push both.
3. CI (`.github/workflows/tests.yml`: offline suite + dependency-audit via
   pip-audit) must be green; the release workflow builds and publishes all
   channels.
4. Verify each channel: `pip install pushframe==X.Y.Z` resolves, the APT
   index serves the version, the GitHub release lists the `.deb`.

There is no release requirement for doc-only changes — they can wait for the
next natural release.
