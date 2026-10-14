---
phase: quick-261014-nim
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - pushframe/utils/names.py
  - pushframe/schedule.py
  - pushframe/pairs.py
  - pushframe/cli.py
  - pushframe/gsync.py
  - pushframe/google/cache.py
  - tests/test_schedule.py
  - tests/test_pairs.py
  - tests/test_google_cache.py
  - tests/test_gsync_engine.py
  - tests/test_gsync_execute.py
  - tests/test_cli_google_album.py
  - tests/test_cli_inspect.py
  - tests/test_cli_reconcile.py
  - tests/test_cli_sync.py
  - docs/ERRORS.md
  - docs/CLI.md
autonomous: true
requirements: [NIM-S1, NIM-S2, NIM-S3, NIM-B4, NIM-B5]
gap_closure: false

estimate:
  tokens: 100000
  raw_tokens: 100000
  tasks: 3
  confidence: low

must_haves:
  truths:
    - "NIM-S1: for ordinary values the ExecStart that `schedule add` writes is BYTE-IDENTICAL to 5.1.33's — pinned literals: `/opt/pf/bin/pushframe google-sync \"Cadre\" --frame \"Cadre de Fabrice\" --apply --yes --scheduled --batch-size 5 --report ERROR --report-tag \"nightly\" --report-to \"me@example.invalid\"` and `/opt/pf/bin/pushframe sync \"/srv/photos\" --frame \"Salon\" --apply --yes --scheduled --report-tag \"nightly\"` (the live venus unit has exactly this shape)."
    - "NIM-S1: every ExecStart element goes through ONE helper `schedule._systemd_arg` (systemd.service(5) COMMAND LINES rules: `\\` -> `\\\\` and `\"` -> `\\\"` inside double quotes, `%` -> `%%`, `$` -> `$$`); `schedule.exec_start_argv(text)` (the inverse, used by `pushframe status`) round-trips hostile album / frame / sync_dir / report_to / command-path values (quotes, backslashes, `%`, `$HOME`, `${X}`, `;`, spaces, non-ASCII) to the exact original argv."
    - "NIM-S1: a control character (unicodedata category Cc: newline, CR, tab, NUL, DEL, C1) in any ExecStart value, in `--at`, or a `$` in the executable path -> `ScheduleError` naming the field with a remedy; `schedule add` prints `schedule: ...`, returns 1, writes ZERO unit files and calls systemctl ZERO times (all unit text is rendered before the first write). Unit files are written as UTF-8."
    - "NIM-S1/S2: one name validator (`pushframe/utils/names.py`, `re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')`) governs pair names AND schedule job names: `cadre-venus`, `nightly`, `living-room-nightly`, `a-pair`, `live-a`, `job_2`, `v1.2` valid; ``, `.`, `..`, `.hidden`, `../x`, `a/b`, `-x`, `cafe` with an accent, `a\\nb`, 65 chars invalid. Enforced at `pair_add` (PairError), `pair_state_paths` (PairError naming `pushframe config pair remove`), `schedule_add`/`schedule_remove`/`render_service`/`render_timer`, and `_rotate_job_log_if_large` (returns False, touches nothing); `pair_remove` stays unvalidated so the remedy works on a hand-edited config.json."
    - "NIM-S2: `google-sync --pair X` / `--all` with a hand-edited unsafe pair name records that pair as FAILED with the named PairError and continues the loop (D-02) — exit 1, no traceback, no directory created."
    - "NIM-S3: a Google media id outside `[A-Za-z0-9_-]{1,255}` is a per-item `CacheOutcome.failed` entry — no `=d` request for it, no write, no unlink outside the cache dir (a pre-existing sibling `victim` file survives byte-identical; today's orphan cleanup deletes it); `prune_cache` skips unsafe ids; `default_cache_dir` refuses an unsafe album token with a named SafeSyncError; failure lines print `(unsafe media id)` instead of the raw id. Real ids (AF1Qip + 38-44 base64url chars, checked against the local manifests) are unaffected."
    - "NIM-B4: manifest drift still fails THIS run (exit 1, nothing uploaded or hidden) but the message names the count and the redact_link'ed ids (never raw) and a self-healing remedy; a dry-run leaves the manifest file byte-identical and says what --apply would do; an --apply run forgets exactly the drifted entries (persisted 0600 via GoogleManifest.save, other entries kept); the NEXT --apply run re-downloads only those items, re-uploads them, exits 0 and re-records them. run_google_sync_plan and compute_plan stay pure."
    - "NIM-B5: resolve_album and resolve_frame resolve exact case-sensitive name -> exact case-insensitive name -> case-insensitive substring (-> frame id fallback); target `Cadre` with albums `Cadre` + `Cadre 2025` resolves `Cadre`; a tie inside any tier is `ambiguous` with exactly that tier's candidates (no silent pick); share-URL / AF1Qip path and the substring-ambiguous-means-no-id-fallback rule unchanged."
    - "Full offline suite green: `uv run --extra dev pytest -q -m \"not live\"` — current count plus the new tests, zero failures, DeprecationWarnings still errors; no version / CHANGELOG / uv.lock change."
  artifacts:
    - path: pushframe/utils/names.py
      provides: "safe_name_problem(name, *, what) -> str | None — the single pair/job name rule"
    - path: pushframe/schedule.py
      provides: "_systemd_arg(value, *, what, bare_ok=False) quoting helper; exec_start_argv(text) inverse; validated job/at; render-before-write schedule_add; validated schedule_remove"
    - path: pushframe/pairs.py
      provides: "pair_add and pair_state_paths enforce safe_name_problem"
    - path: pushframe/google/cache.py
      provides: "is_safe_media_id(gid) -> bool; _download_one / orphan cleanup / prune_cache never build a path from an unsafe id"
    - path: pushframe/gsync.py
      provides: "SafeSyncError.drifted_ids + manifest_drift(drifted_ids); format_drift_remedy(count, *, evicted); --apply eviction in run_google_sync; default_cache_dir guard; per-pair PairError isolation in the --pair/--all loop"
    - path: pushframe/cli.py
      provides: "resolve_album/resolve_frame exact-first tiers; status ExecStart parsing via schedule.exec_start_argv; rotate guard"
  key_links:
    - from: pushframe/schedule.py _exec_start_for
      to: pushframe/schedule.py _systemd_arg
      via: "every parts element (command path bare_ok=True with `$` refused, constants, report level, batch size bare; album/frame/sync_dir/job/report_to quoted)"
    - from: pushframe/cli.py _describe_exec_start / _unit_argv_parse_problem
      to: pushframe/schedule.py exec_start_argv
      via: "replaces the bare shlex.split so `%%`/`$$` render as `%`/`$` and ValueError semantics are kept"
    - from: pushframe/gsync.py run_google_sync (except SafeSyncError after run_google_sync_plan)
      to: pushframe/google/manifest.py GoogleManifest.save
      via: "if e.drifted_ids and apply: pop those ids from manifest.entries, manifest.save(manifest_path); print format_drift_remedy(len, evicted=apply); return 1"
    - from: pushframe/pairs.py pair_state_paths
      to: pushframe/utils/names.py safe_name_problem
      via: "raise PairError before any Path is built"
---

<objective>
Fix five findings from the 2026-10-14 code review (verified against master 9610c30), each with red tests first:

- NIM-S1 — `pushframe/schedule.py` `_exec_start_for` wraps user values in naive double quotes: a `"`, `\`, newline, `%` (specifier) or `$` (variable) breaks the argv or injects unit directives. One systemd-correct quoting helper, control characters refused, byte-identical output for ordinary values.
- NIM-S2 — `pushframe/pairs.py` accepts any non-empty pair name, and pair names become path components (`pairs/<name>/`). One strict validator shared with schedule job names (unit file names), enforced at creation and at every path-building use site.
- NIM-S3 — `pushframe/google/cache.py` uses Google media ids directly as file names (`cache_dir / google_media_id`) for the write, the orphan cleanup and the prune. An unsafe id becomes a per-item failure, never a path.
- NIM-B4 — `pushframe/gsync.py` manifest drift (photo deleted from the frame in the Aura app) fails every following run forever. It still fails loud, but names the items and evicts the drifted manifest entries on an --apply run, so the next run re-downloads and re-uploads them.
- NIM-B5 — `pushframe/cli.py` `resolve_album` (and `resolve_frame`, same flaw) matches substrings first, so `Cadre` is ambiguous next to `Cadre 2025`. Exact match first.

Purpose: unattended scheduled runs must never be steerable into writing unit directives or files outside their state dirs, must never deadlock on recoverable state drift, and must never pick (or refuse) the wrong album.
Output: hardened modules plus offline TDD tests, docs/ERRORS.md and docs/CLI.md rows, full offline suite green. No version bump, no CHANGELOG (the release is a separate step).
</objective>

<execution_context>
@~/.claude/gsd-core/workflows/execute-plan.md
@~/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@./CLAUDE.md
@pushframe/schedule.py
@pushframe/pairs.py
@pushframe/google/cache.py
@pushframe/google/manifest.py
@pushframe/utils/io.py

<interfaces>
<!-- Extracted at planning time from master 9610c30. Line numbers approximate. -->

pushframe/schedule.py (239 lines):
- `UNIT_DIR`, `LOG_DIR` module globals (tests monkeypatch them via the `unit_dir` fixture in tests/test_schedule.py); `class ScheduleError(Exception)`.
- `render_service(job, exec_start) -> str` (~L66: `Description=pushframe scheduled job '{job}'`, `ExecStart={exec_start}`, `StandardOutput=append:{LOG_DIR/job.log}`); `render_timer(job, every=None, at=None) -> str` (~L84: `OnCalendar={at or every_to_oncalendar(every)}`, `Unit=pushframe-{job}.service`).
- `_pushframe_command() -> str` (~L107: shutil.which / argv0 / bare 'pushframe').
- `_exec_start_for(pair=None, album=None, frame=None, sync_dir=None, batch_size=None, job=None, report=None, report_to=None) -> str` (~L126-155): today builds `parts` with f'"{value}"' and f'--frame "{frame}"' and joins with ' '. Pair mode reads the spec from `config_store.load()['pairs']`; an unconfigured pair renders album/frame as `"None"` (tests/test_schedule.py::test_exec_start_uses_absolute_executable relies on it not crashing — coerce with str()).
- `schedule_add(job, *, pair, album, frame, sync_dir, every, at, batch_size, report, report_to) -> int` (~L158): job check `job.replace('-', '').isalnum()` (Unicode-permissive) ~L165; try/except(Exception) around pair_resolve + render_timer ~L194-201; `_exec_start_for` and the two `write_text` calls are OUTSIDE the try (~L203-210) — a raise there is a traceback with a half-written unit pair.
- `schedule_remove(job) -> int` (~L227): no validation; builds `UNIT_DIR / f'pushframe-{job}.service'`.

pushframe/cli.py:
- `_unit_argv_parse_problem(exec_line)` ~L1123 and `_describe_exec_start(exec_line)` ~L1178 both do `shlex.split(exec_line.removeprefix('ExecStart='))` with `except ValueError` fallbacks ('ExecStart is not shell-quoted correctly' / '(unreadable unit)').
- `_rotate_job_log_if_large(report_tag, *, limit_bytes, keep)` ~L2316: `log = LOG_DIR / f'{report_tag}.log'` (report_tag = the CLI `--report-tag`, i.e. the job name the unit passes).
- `FrameResolution` / `resolve_frame(target, frames)` ~L1304-1341 (substring tier, then exact id); `AlbumResolution` / `resolve_album(target, albums)` ~L1344-1389 (share URL / `AF1Qip[A-Za-z0-9_-]{20,}` direct path, then substring tier using `.lower()`; `a.title` may be None).
- `config pair add` path ~L479-496 prints `pair not added: {e}` (rc 1) on any pair_add exception, then calls pair_state_paths for the success message.

pushframe/pairs.py: `PairError`; `pair_add(name, *, album, frame)` (~L30, only an empty check); `pair_remove(name) -> bool`; `pair_resolve(name) -> dict`; `pair_state_paths(name) -> (manifest, cache)` (~L95: `~/.config/pushframe/pairs/<name>/google-manifest.json`, `~/.local/state/pushframe/pairs/<name>/cache`).

pushframe/gsync.py (801 lines):
- `SENTINEL_SUFFIX = ".absent"`; `default_cache_dir(album_share_token)` ~L55; `SafeSyncError(RuntimeError)` classmethods ~L63-109, `manifest_drift()` ~L104 (message starts "manifest claims an upload was confirmed but the frame reports no matching md5_hash (state drift)" — tests/test_gsync_engine.py matches "manifest claims an upload").
- `build_demand(...)` ~L132: manifest member without staged file -> demand path `Path(cache_dir) / f"{gid}{SENTINEL_SUFFIX}"` (a fictional path: never opened, written or unlinked); returns `(demand {md5: [path]}, failures, videos)`.
- `run_google_sync_plan(...)` ~L180-209 (pure): `if any(p.name.endswith(SENTINEL_SUFFIX) for p in plan.to_upload): raise SafeSyncError.manifest_drift()`.
- `format_plan_report(...)` ~L212: failure lines `f"  {redact_link(gid)}: {err}"` ~L253.
- `run_google_sync(...)` pair loop ~L380-403 (`spec = pair_resolve(name)`, `m_path, c_path = pair_state_paths(name)`, header print, recursive call, results); `cdir = Path(cache_dir) if cache_dir else default_cache_dir(album.album_id)` ~L527; plan try/except ~L543-550 (`print(f'google-sync failed: {e}')`, return 1); `if not apply: return 0` ~L555.
- Lazy-import convention: gsync imports `pushframe.google.cache` names INSIDE functions (see build_demand) — keep it.

pushframe/google/cache.py: `_download_one(session, item, cache_dir, expected_size)` ~L53 (GET then `path = cache_dir / google_media_id` ~L77, `write_private_bytes`); `download_to_cache` orphan cleanup ~L126-130 (`orphan = cache_dir / google_media_id; ... orphan.unlink()`); `prune_cache(cache_dir, manifest_ids)` ~L141 (unlink by name, counts missing as pruned).

pushframe/google/manifest.py: `GoogleManifest.entries: dict[str, dict]` (public dataclass field; gsync already reads it), `entry_for`, `add`, `save(path)` (ensure_private_dir + 0600 atomic replace). No change needed.

pushframe/google/redaction.py: `redact_link(s)` -> `AF1Qip…XXXX` for a full token (AF1Qip + 40+ chars); other strings pass through raw (hence the `(unsafe media id)` label).

Test harness conventions:
- tests/test_schedule.py fixtures `unit_dir` (patches UNIT_DIR + LOG_DIR to tmp) and `cfg_path` (patches settings.CONFIG_PATH); systemctl faked via `monkeypatch.setattr(sch, 'run_systemctl', ...)` + `sch.systemd_user_session_ok -> True`; `_rotate_job_log_if_large` tests ~L301.
- tests/test_pairs.py fixture `cfg_path`; `config_store.update(pairs={...})` writes a pair WITHOUT validation (use it to simulate a hand-edited config.json).
- tests/test_gsync_all.py's `cfg_path` fixture monkeypatches `pairs_mod.pair_state_paths` with a lambda — do NOT put the unsafe-pair gsync test there (the lambda bypasses validation); put it in tests/test_pairs.py with `monkeypatch.setenv('HOME', str(tmp_path))`.
- tests/test_google_cache.py helpers `_item(n)` (id `AF1QipFAKEitem{n:06d}` + 30 zeros), `_listing(items)`, `_Session`, `_Router` (extend with a request log if it has none).
- tests/test_gsync_engine.py helpers `_gid(n)`, `_item(n)`, `_listing(items, exhausted=True)`, `_asset(id_, md5)`, `_manifest_for(*ns)` (md5 = get_md5(f"photo-{n}".encode())), `CacheOutcome()`.
- tests/test_gsync_execute.py `_GoogleRouter(item_bodies=..., ...)` with `download_requests`; `_FakeAura(assets)`; `_assets_for(n)` (ids `frame-1..n`, md5 = get_md5(_jpeg(i))); `_FakeS3()`, `_FakeSQS()`; call shape `run_google_sync("Cadre", "Fabrice", apply=True, yes=True, session=_google_session(router), aura=aura, s3_client=_FakeS3(), sqs_client=_FakeSQS(), cache_dir=tmp_path / "cache", manifest_path=tmp_path / "manifest.json")`; see test_second_run_zero_uploads_and_zero_downloads ~L491 for a two-run pattern.
- tests/test_cli_inspect.py / test_cli_reconcile.py / test_cli_sync.py each have an ambiguity test using target `'kitchen'` against frames `Kitchen` + `Kitchen 2 Upstairs` (inspect ~L92, reconcile ~L146, sync ~L153).
- tests/test_cli_google_album.py `ALBUMS` (Vacances Corse / Vacances Bretagne / Famille 2024) + `AlbumSummary(album_id=..., title=..., item_count=...)`.

Evidence gathered at planning time:
- systemd 259 man page systemd.service(5) COMMAND LINES: `%` specifiers are expanded on the whole command line; a literal `$` is `$$`; "the first argument (i.e. the program to execute) may not be a variable" (so `$` in the executable path cannot be escaped — refuse it); "The command to execute may contain spaces, but control characters are not allowed"; a bare `;` word must be `\;` (our user values are always double-quoted, so a `;` value is a quoted word, not a separator).
- The live unit on this host: `ExecStart=<venv>/bin/pushframe google-sync "Cadre" --frame "Cadre de Fabrice" --apply --yes --scheduled --report ERROR --report-tag "nightly"` — the pin shape.
- Local manifests (stats only, no ids printed): 24 ids of length 44 and test-run shards of length 50, all `AF1Qip`-prefixed, all `[A-Za-z0-9_-]`. Local pair names: a-first, b-second, b-works, cadre-venus — all valid under the new rule.
</interfaces>
</context>

<tasks>

<task type="tracer" tdd="true">
  <name>Task 1 (tracer): one name rule + systemd-correct ExecStart quoting, end to end from `schedule add` to the unit text on disk to `pushframe status` (NIM-S1, NIM-S2)</name>
  <files>pushframe/utils/names.py, pushframe/schedule.py, pushframe/pairs.py, pushframe/cli.py, pushframe/gsync.py, tests/test_schedule.py, tests/test_pairs.py, docs/ERRORS.md</files>
  <read_first>
    - pushframe/schedule.py (whole file, 239 lines)
    - pushframe/pairs.py (whole file, 102 lines)
    - pushframe/cli.py lines 1063-1210 (status schedule section, _unit_argv_parse_problem, _describe_exec_start) and 2316-2345 (_rotate_job_log_if_large)
    - pushframe/gsync.py lines 335-403 (pair / --all loop)
    - tests/test_schedule.py lines 1-45 (fixtures), 130-145, 167-230, 295-320
    - tests/test_pairs.py lines 1-30 and 105-140
    - docs/ERRORS.md sections "Config & pairs" (~L75) and "Scheduling" (~L98)
  </read_first>
  <behavior>
    RED first (commit `test(quick-261014-nim): red tests for systemd ExecStart quoting and the pair/job name rule`), then GREEN (commit `fix(quick-261014-nim): quote ExecStart per systemd rules; one safe-name rule for pairs and jobs`).

    tests/test_schedule.py (monkeypatch `pushframe.schedule._pushframe_command` to return `/opt/pf/bin/pushframe` unless stated):
    - test_exec_start_byte_identical_for_ordinary_values (characterization pin, written first, must pass on the CURRENT code before any production edit): pair `cadre-venus` = album `Cadre`, frame `Cadre de Fabrice`; `_exec_start_for(pair='cadre-venus', job='nightly', report='ERROR', report_to='me@example.invalid', batch_size=5)` equals exactly `/opt/pf/bin/pushframe google-sync "Cadre" --frame "Cadre de Fabrice" --apply --yes --scheduled --batch-size 5 --report ERROR --report-tag "nightly" --report-to "me@example.invalid"`; `_exec_start_for(sync_dir='/srv/photos', frame='Salon', job='nightly')` equals exactly `/opt/pf/bin/pushframe sync "/srv/photos" --frame "Salon" --apply --yes --scheduled --report-tag "nightly"`; `render_service('nightly', x)` still contains the line `Description=pushframe scheduled job 'nightly'`.
    - test_exec_start_round_trips_hostile_values: album with a double quote, a backslash (including a trailing one), `100%`, `$HOME`, `${X}`, a single quote, non-ASCII (`été`); frame `Salon; rm -rf ~`; report_to `a"b%c$d@example.invalid`; a second case where frame is exactly `;`. `schedule.exec_start_argv(_exec_start_for(...))` equals the exact intended argv list (command, verb, album, `--frame`, frame, flags..., `--report-to`, report_to). The raw text contains `100%%`, `$$HOME` and an escaped quote; it contains no bare `%` that is not doubled.
    - test_executable_path_quoting: command `/home/John Doe/100%/pushframe` -> the raw ExecStart starts with `"/home/John Doe/100%%/pushframe" ` and argv[0] round-trips; command `/opt/$x/pushframe` -> ScheduleError naming the executable path.
    - test_control_characters_refused_named: for each of `\n`, `\r`, `\t`, `\x00`, `\x7f`, `\x85` inside album (and once inside frame, sync_dir, report_to) -> `_exec_start_for` raises ScheduleError whose message names the field and says `control character`.
    - test_schedule_add_refuses_injection_without_writing: pair stored via `config_store.update` with album `A\nExecStartPre=/bin/evil`; also `at='daily\nUnit=evil.service'`; also report_to with a newline. Each `schedule_add(...)` returns 1, prints `schedule:` and `control character`, leaves NO `pushframe-*` file in UNIT_DIR, and the faked run_systemctl records zero calls.
    - test_job_names_one_rule: invalid jobs (``, `.`, `..`, `../x`, `a/b`, `-x`, `café`, `a\nb`, 65 chars) -> schedule_add returns 1 with no file and no systemctl call; schedule_remove returns 1 with no systemctl call; render_service and render_timer raise ScheduleError. Valid jobs (`nightly`, `cadre-venus`, `living-room-nightly`, `job_2`, `v1.2`) install normally.
    - test_status_unescapes_systemd_quoting: `_describe_exec_start('ExecStart=' + hostile_exec)` shows the album with a single `%` and `$HOME` (no `%%`, no `$$`); `_unit_argv_parse_problem` returns None for that line; the existing describe/parse tests stay green.
    - test_rotate_refuses_unsafe_tag: with an oversized file at `LOG_DIR.parent / 'x.log'`, `_rotate_job_log_if_large('../x', limit_bytes=1)` returns False and that file is untouched; `nightly` rotation unchanged.

    tests/test_pairs.py:
    - test_pair_names_one_rule: the invalid set above -> `pair_add` raises PairError (message names the allowed characters), config unchanged; `cadre-venus`, `a-pair`, `live-a`, `salon`, `b.2`, `x_y` accepted.
    - test_state_paths_refuse_hand_edited_names: `config_store.update(pairs={'../evil': {'album': 'A', 'frame': 'F'}})` -> `pair_state_paths('../evil')` raises PairError mentioning `pair remove`; `pair_remove('../evil')` returns True (the remedy works).
    - test_gsync_unsafe_pair_fails_named_and_loop_continues: `monkeypatch.setenv('HOME', str(tmp_path))`, the same hand-edited pair -> `run_google_sync('', '', pair='../evil')` and `run_google_sync('', '', run_all=True)` each return 1, print the PairError text and `[FAILED]`, raise nothing, and create nothing under tmp_path besides the config file (no vault or network is reached).
  </behavior>
  <action>
    Create pushframe/utils/names.py (stdlib only, snake_case util module per CLAUDE.md conventions) with `safe_name_problem(name, *, what: str) -> str | None`: None when `name` is a str matching `re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', name)`. Use fullmatch, never `re.match` with a trailing `$`, because `$` accepts a trailing newline. Otherwise return one sentence: what, then the repr of the name (repr keeps control characters visible and inert in logs), then the rule (1-64 ASCII letters, digits, ".", "_" or "-", starting with a letter or digit), then why (it becomes a file, directory and systemd unit name; a leading "-" would parse as a flag), then an example (cadre-venus). The module docstring records the why: per-pair state dirs, unit file names, the argv token, and ASCII-only because systemd unit names are ASCII. Per S2 the existing prod pair `cadre-venus` and the job `nightly` must stay valid.

    pushframe/schedule.py (S1):
    (1) Add `_systemd_arg(value, *, what: str, bare_ok: bool = False) -> str`, the ONE helper for an ExecStart argv element. Coerce with str() so None and int keep today's rendering. If any character has unicodedata category `Cc`, raise ScheduleError naming `what` with the value's repr: it contains a control character, unit files are line-based so it would break the unit or inject directives, nothing was written, remove the character from the value. Then double `%` and `$` (systemd specifier and variable escapes, see the man-page evidence in <interfaces>). If bare_ok is set and the value is non-empty and has no whitespace, no `"`, no `'` and no backslash, return it bare. Otherwise escape backslash first, then `"`, and wrap in double quotes.
    (2) Add the public inverse `exec_start_argv(text: str) -> list[str]`: `shlex.split` (POSIX: inside double quotes it undoes exactly the backslash and quote escapes), then per token turn `%%` into `%` and `$$` into `$`. Let shlex's ValueError propagate, because the cli callers rely on it.
    (3) Rewrite `_exec_start_for` so EVERY element passes through `_systemd_arg`. The command path uses bare_ok=True and is refused with ScheduleError if it contains `$`: systemd does not variable-expand the executable, so no escape exists. The remedy names a reinstall location without `$`, e.g. `uv tool install` into ~/.local/bin. Constant verbs and flags, the normalized report level and the batch size use bare_ok=True; for these it is a no-op that keeps the rule uniform. album, frame, sync_dir, job and report_to are always quoted. `--frame` and its value become two separate parts: the joined text is identical to today's f'--frame "{frame}"'. The element order stays exactly as today so the pin holds.
    (4) Validate `job` with `safe_name_problem(job, what='job name')` in render_service and render_timer (they are public pure renderers, so this is defense in depth; raise ScheduleError). render_timer also refuses a Cc character in `at` with ScheduleError, because OnCalendar= is a line in the .timer file. Leave the StandardOutput log path untouched (LOG_DIR is the operator's own home; see the threat register).
    (5) schedule_add: replace the `isalnum` check with safe_name_problem and print `schedule: <problem>` then return 1. ERRORS.md keys on the `schedule: job name` prefix, so keep it. Move `_exec_start_for` plus `render_service` INSIDE the existing try, next to render_timer, so all three unit texts are rendered before `UNIT_DIR.mkdir` and before the first write. Any refusal therefore prints `schedule: {e}`, returns 1 and writes nothing. Write both unit files with `encoding='utf-8'`: status already reads them as UTF-8, and Python 3.14's locale default may be ASCII. schedule_remove validates the job the same way. On failure it prints the problem plus "nothing removed; a unit with such a name can only be hand-made: delete it from ~/.config/systemd/user/", returns 1 and makes no systemctl call.

    pushframe/cli.py: `_unit_argv_parse_problem` and `_describe_exec_start` call `schedule.exec_start_argv` (lazy import, as the module does elsewhere) instead of the bare `shlex.split`, keeping their `except ValueError` fallbacks verbatim. `_rotate_job_log_if_large` returns False (touching nothing) when `safe_name_problem(report_tag, what='job name')` is not None. Do this right after the existing `if not report_tag` guard; a rotation problem must never fail a run (existing contract).

    pushframe/pairs.py (S2): pair_add keeps its empty-name message and then raises PairError with the safe_name_problem text (what='pair name'). pair_state_paths raises PairError BEFORE building any Path: the problem text plus the remedy "this name was not created by `config pair add` (hand-edited config.json?) — remove it with `pushframe config pair remove <name>` and re-add it under a valid name". pair_remove stays unvalidated, with a one-line comment saying why: it is the remedy, so it must accept whatever the file holds. Mention the rule in the module docstring.

    pushframe/gsync.py (S2, pair loop only): inside `for name in sorted(names)`, call pair_state_paths in a try BEFORE the `=== pair [...] ===` header. On PairError print `google-sync: {e}`, append `(name, 1)` and `continue`. This honors D-02: one pair's failure never aborts the loop, and the `--all report` lists it FAILED.

    docs/ERRORS.md: update the `schedule: job name "…" must be alphanumeric/dashes` row to the new message and rule. Add a Scheduling row for `schedule: … contains a control character …` (nothing was written; remove the character) and for the `$`-in-executable-path refusal. Add a Config & pairs row for the pair-name refusal (`pair not added: pair name '…' must be …`) and for the hand-edited-name PairError (remedy: `pushframe config pair remove <name>`, then re-add). Do not mention a version number.
  </action>
  <verify>
    <automated>uv run --extra dev pytest -q -m "not live" tests/test_schedule.py tests/test_pairs.py tests/test_gsync_all.py tests/test_preflight_sweep.py tests/test_report.py</automated>
  </verify>
  <done>The pin test passes before and after (byte-identical ordinary ExecStart). Every hostile value round-trips through exec_start_argv or is refused by name with zero files written and zero systemctl calls. One name rule governs pairs and jobs at creation and at every path-building site. A hand-edited unsafe pair fails named inside the --pair/--all loop without aborting it. Red and green commits are made.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Google media ids never become unsafe paths; manifest drift fails loud once, names the items, and self-heals on --apply (NIM-S3, NIM-B4)</name>
  <files>pushframe/google/cache.py, pushframe/gsync.py, tests/test_google_cache.py, tests/test_gsync_engine.py, tests/test_gsync_execute.py, docs/ERRORS.md</files>
  <read_first>
    - pushframe/google/cache.py (whole file, 167 lines)
    - pushframe/gsync.py lines 1-260 (docstring, default_cache_dir, SafeSyncError, build_demand, run_google_sync_plan, format_plan_report) and 525-556 (cache dir, downloads, plan try/except, dry-run return)
    - tests/test_google_cache.py lines 20-110 (helpers, _Router, failure tests)
    - tests/test_gsync_engine.py lines 30-75 and 150-165 (helpers, existing drift test) and 275-285 (default_cache_dir test)
    - tests/test_gsync_execute.py lines 36-60, 100-240 (fakes) and 491-515 (two-run pattern)
  </read_first>
  <behavior>
    RED first (commit `test(quick-261014-nim): red tests for unsafe media ids and the manifest-drift deadlock`), then GREEN (commit `fix(quick-261014-nim): unsafe media ids are per-item failures; --apply forgets drifted manifest entries`).

    tests/test_google_cache.py:
    - test_unsafe_media_id_is_a_per_item_failure_never_a_path: a sibling file `tmp_path / 'victim'` holding b'keep'; cache_dir `tmp_path / 'cache'`; listing = `_item(1)` plus items with ids `../victim`, `a/b`, `..`, ``, `x` * 256 and an id with a non-ASCII letter (base_url values the router can answer). `download_to_cache` stages only item 1. Every unsafe id is in `failed` with an error naming the unsafe-id refusal (the error text itself does not contain the raw id). The router saw exactly one `=d` request (item 1). `victim` is byte-identical. `tmp_path` contains only `cache` and `victim`. The cache dir holds only item 1's file. On today's code this is RED: the orphan cleanup unlinks `victim`.
    - test_prune_skips_unsafe_ids: `prune_cache(cache_dir, {'../victim', _gid-of-item-1})` leaves victim intact and returns 1.
    - test_real_shape_ids_are_safe: `is_safe_media_id` is True for `_item(n)['id']`, for `'AF1Qip' + 'A' * 38` and for a `_-` mix; False for non-str.

    tests/test_gsync_engine.py:
    - test_manifest_drift_names_items_and_carries_ids: listing [1, 2, 3], `_manifest_for(1, 2, 3)`, frame holds the md5s of 1 and 3 -> `run_google_sync_plan` raises SafeSyncError with `e.drifted_ids == (_gid(2),)`; str(e) still starts with `manifest claims an upload`, contains `1 item` and `redact_link(_gid(2))`, and does NOT contain `_gid(2)`; `manifest.entries` is unchanged afterwards (the pure half never mutates). Two drifted items -> both ids in listing order.
    - test_other_safe_sync_errors_have_no_drift_ids: empty-listing SafeSyncError has `drifted_ids == ()`.
    - test_format_drift_remedy_wording: `format_drift_remedy(2, evicted=True)` mentions `forgot 2`, re-download / re-upload, and that nothing is hidden or deleted; `format_drift_remedy(1, evicted=False)` mentions `--apply` and that this dry-run changed nothing.
    - test_default_cache_dir_refuses_unsafe_token: `../x` and `a/b` raise SafeSyncError; the existing `TOKEN` test is unchanged.
    - test_failure_lines_never_print_unsafe_ids: `format_plan_report` with failures `[('a\nb', 'boom')]` contains `(unsafe media id)` and no raw newline-bearing id; a safe AF1Qip id still prints its redact_link shape.

    tests/test_gsync_execute.py:
    - test_manifest_drift_self_heals_over_two_apply_runs. Run 1: `--apply --yes`, frame `_FakeAura(_assets_for(3))` -> rc 0, manifest holds gids 1-3. Then the user deletes photo 2 on the frame: a fresh `_FakeAura` whose assets omit `frame-2`. Run 2, dry-run: rc 1, output has `state drift`, `1 item` and `--apply`, the manifest file bytes are identical to after run 1, and no upload call happens. Run 3, `--apply --yes`: rc 1, output says `forgot 1`, the manifest has gids 1 and 3 but not 2, the file mode is 0o600, and the fake recorded no upload, select or exclude call. Run 4, `--apply --yes` with a fresh router: rc 0, exactly one `=d` request (item 2), `Applied: 1 uploaded`, and the manifest holds gid 2 again.
  </behavior>
  <action>
    pushframe/google/cache.py (S3): add the module-level pattern `[A-Za-z0-9_-]{1,255}`. 255 is NAME_MAX, the cache file name's real ceiling; real ids are AF1Qip plus base64url. Add `is_safe_media_id(gid) -> bool`: isinstance str and re.fullmatch. `_download_one` checks it FIRST, before the `=d` GET, and raises ValueError with a message that does not echo the id: "unsafe media id (characters outside [A-Za-z0-9_-] or longer than 255) — refusing to use it as a cache file name; nothing downloaded". download_to_cache's existing per-item `except Exception` records it in `failed`: a per-item failure, never a crash. The orphan cleanup and prune_cache skip any id for which is_safe_media_id is False: no Path is built, nothing is unlinked, and prune does not count it. Add one module-docstring sentence: ids become file names only after this check.

    pushframe/gsync.py (S3): `default_cache_dir` raises SafeSyncError ("album share token is not a safe directory name — refusing to build a cache path from it") when `is_safe_media_id(album_share_token)` is False. Use a lazy import, per the module's convention. In run_google_sync, compute `cdir` inside a try that prints `google-sync failed: {e}` and returns 1, matching the surrounding pattern. Add a private `_gid_label(gid)`: `redact_link(gid)` for a safe id, else the literal `(unsafe media id)`. Use it in format_plan_report's failure lines and in the drift message. In build_demand, add a comment recording the audit conclusion: the sentinel path is fictional, is never opened, written or unlinked, and only its `.name` suffix marks it.

    pushframe/gsync.py (B4), keeping run_google_sync_plan and compute_plan pure:
    (1) SafeSyncError gets the class attribute `drifted_ids: tuple[str, ...] = ()`, so every SafeSyncError answers it.
    (2) `manifest_drift(cls, drifted_ids=())` sets `err.drifted_ids = tuple(drifted_ids)`. Its message keeps the leading sentence `manifest claims an upload was confirmed but the frame reports no matching md5_hash (state drift)`. It continues with `for N item(s): <labels>` (at most 10 labels via _gid_label, then `+K more`), and with "usually the photo was deleted from the frame in the Aura app; nothing was uploaded or hidden this run (safety gate)". Drop the dead-end "investigate before re-running".
    (3) In run_google_sync_plan, after compute_plan, map sentinel paths back to md5s through the demand map (path to md5 from `demand.items()`). Then collect, in listing order and de-duplicated, every listing item id whose manifest entry's md5_hash is one of the drifted md5s. Do NOT parse ids out of `p.name`: an unusual id would map wrongly. Raise `SafeSyncError.manifest_drift(drifted_ids)` under the same condition as today.
    (4) Add a pure `format_drift_remedy(count, *, evicted: bool) -> str`. evicted=True: "Recovery: forgot N manifest entr(y|ies) for those items — the next run re-downloads them and re-uploads only what the frame does not hold (additive: nothing is hidden or deleted); re-run google-sync or let the next scheduled run heal it". evicted=False: "Recovery: re-run with --apply to forget those N manifest entr(y|ies) (this dry-run changed nothing); the run after that re-downloads them and re-uploads only what the frame does not hold".
    (5) In run_google_sync's `except SafeSyncError as e` around run_google_sync_plan: after the existing `google-sync failed: {e}` print, if `e.drifted_ids` is non-empty and `apply` is true, pop each drifted id from `manifest.entries` and persist with `manifest.save(manifest_path)`. That is the existing atomic 0600 path; per B4 do not hand-roll a write. Then print `format_drift_remedy(len(e.drifted_ids), evicted=apply)` and return 1 as today. A dry-run never mutates. Eviction is local state only and happens before any confirmation gate on purpose. The following run still passes every SAFE gate, the y/N gate and the write budget before writing anything. An already-held md5 is re-recorded without an upload by the existing persistence code. Say this in a comment at the eviction site.
    (6) Update the module docstring's last paragraph: "fails loud" stays, and add "an --apply run forgets the drifted entries so the next run re-downloads them".

    docs/ERRORS.md, Google sync section: add a row for `manifest claims an upload was confirmed … (state drift) for N item(s)`. Meaning: the manifest says the frame holds a photo that the frame no longer reports, usually because it was deleted in the Aura app. What to do: the dry-run prints the remedy; an --apply run, including the nightly, forgets those entries and the next run re-uploads them. Add a row for the `(unsafe media id)` per-item failure. No version numbers.
  </action>
  <verify>
    <automated>uv run --extra dev pytest -q -m "not live" tests/test_google_cache.py tests/test_gsync_engine.py tests/test_gsync_execute.py tests/test_gsync_all.py tests/test_report.py tests/test_private_paths.py</automated>
  </verify>
  <done>An unsafe media id or album token never becomes a filesystem path; it is a named per-item failure or refusal, and the victim-file test proves no write or unlink escapes the cache. Drift exits 1 with the count, redacted ids and a remedy. A dry-run leaves the manifest byte-identical. An --apply run evicts exactly the drifted entries (0600), and the following --apply run re-uploads them and exits 0. run_google_sync_plan stays pure. Red and green commits are made.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: exact name beats substring in resolve_album and resolve_frame (NIM-B5)</name>
  <files>pushframe/cli.py, tests/test_cli_google_album.py, tests/test_cli_inspect.py, tests/test_cli_reconcile.py, tests/test_cli_sync.py, docs/CLI.md, docs/ERRORS.md</files>
  <read_first>
    - pushframe/cli.py lines 1304-1390 (FrameResolution, resolve_frame, AlbumResolution, resolve_album)
    - tests/test_cli_google_album.py lines 1-70
    - tests/test_cli_inspect.py lines 88-120, tests/test_cli_reconcile.py lines 146-165, tests/test_cli_sync.py lines 153-172
    - docs/CLI.md lines 80-100 (the `--frame` resolution table); grep docs/CLI.md for the album resolution wording
    - docs/ERRORS.md "Frame and album resolution" (~L43-48)
  </read_first>
  <behavior>
    RED first (commit `test(quick-261014-nim): red tests for exact-name-first album and frame resolution`), then GREEN (commit `fix(quick-261014-nim): exact album/frame names win over substring matches`).

    tests/test_cli_google_album.py (pure resolve_album, AlbumSummary fixtures):
    - `resolve_album('Cadre', [Cadre, Cadre 2025])` resolves `Cadre` (RED today: ambiguous).
    - `resolve_album('cadre', [Cadre, Cadre 2025])` resolves `Cadre` (exact case-insensitive tier).
    - `resolve_album('Cadre', [Cadre, cadre, Cadre 2025])` resolves the case-sensitive `Cadre`.
    - `resolve_album('CADRE', [Cadre, cadre, Cadre 2025])` is ambiguous with candidates exactly [Cadre, cadre]. `Cadre 2025` is not among them.
    - Two albums titled exactly `Cadre` are ambiguous with both (no silent pick).
    - An album with title None among the list never crashes any tier.
    - The existing substring, not-found and direct-link/id tests stay green unchanged.

    tests/test_cli_inspect.py (pure resolve_frame; frames as `types.SimpleNamespace(name=..., id=...)`), mirroring the album cases:
    - `kitchen` vs `Kitchen` + `Kitchen 2 Upstairs` resolves `Kitchen`.
    - `kitch` is ambiguous, and an exact-id target that is also a substring-ambiguous name still does NOT fall back to the id.
    - A target matching no name still resolves by exact id.
    - Same tie rules as albums.

    Existing ambiguity tests: in tests/test_cli_inspect.py (~L92), tests/test_cli_reconcile.py (~L146) and tests/test_cli_sync.py (~L153), change the target `'kitchen'` to `'kitch'`. Add a one-line comment: an exact name now resolves; the test keeps exercising the ambiguous branch. Every assertion stays as it is.
  </behavior>
  <action>
    pushframe/cli.py `resolve_album` (after the unchanged share-URL / AF1Qip direct path) and `resolve_frame` both use the same three name tiers, per B5. Tier 1: titles or names equal to the target (case-sensitive). Tier 2: equal after `.lower()` on both sides; use the same folding as the existing substring tier so the tiers stay consistent. Tier 3: the existing case-insensitive substring tier. The first tier with exactly one match resolves. The first tier with two or more matches returns `ambiguous` with exactly that tier's candidates. That never silently picks (T-17-06), and the D-03 rule holds: a substring-ambiguous frame target never falls back to the id. Only when all three tiers are empty: resolve_frame keeps its exact-id fallback and then `not_found` with every frame, and resolve_album keeps `not_found` with every album. Skip albums whose title is None in every tier. A small private helper shared by both functions is fine, provided both keep their pure dataclass-result contracts. Update both docstrings to list the tiers and the reason: "Cadre" next to "Cadre 2025" must resolve, not be ambiguous.

    docs/CLI.md: in the `--frame` resolution table, add the exact-name rows (an exact case-sensitive, then case-insensitive, name match resolves even when longer names contain it). Apply the same wording wherever album-name resolution is described. docs/ERRORS.md "Several candidates" row: add that an exact full name always wins, so typing the full name resolves it. No version numbers.
  </action>
  <verify>
    <automated>uv run --extra dev pytest -q -m "not live" tests/test_cli_google_album.py tests/test_cli_inspect.py tests/test_cli_reconcile.py tests/test_cli_sync.py tests/test_gsync_execute.py tests/test_gsync_all.py</automated>
  </verify>
  <done>`Cadre` resolves next to `Cadre 2025` for albums, and the same holds for frames. Ties inside a tier stay ambiguous with exactly that tier's candidates. The direct-link path and D-03 are unchanged. The three updated ambiguity tests still cover the ambiguous branch. Docs are updated. Red and green commits are made.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| CLI flags / config.json -> systemd unit files | Operator-typed album, frame, sync_dir, report_to, `--at`, job and pair names, or a hand-edited config.json, are rendered into line-based unit files that systemd parses with its own quoting, `%` specifier and `$` variable rules |
| config.json pair names -> filesystem | Pair names become directory components under ~/.config/pushframe/pairs/ and ~/.local/state/pushframe/pairs/ |
| photos.google.com listing -> cache filesystem | Google media ids from an undocumented upstream (shared albums may come from other accounts) become cache file names that are written and unlinked |
| manifest.json <-> frame listing | Local "confirmed upload" claims vs the frame's reported md5s; a disagreement must neither be silently ignored nor permanently block the unattended mirror |
| album/frame name target -> resolution | A loose target picks the album or frame that scheduled writes will hit |

## STRIDE Threat Register

| Threat ID | Category | Component | Severity | Disposition | Mitigation Plan |
|-----------|----------|-----------|----------|-------------|-----------------|
| T-261014-nim-01 | Tampering | schedule._exec_start_for / render_timer: newline, `"`, `\`, `%`, `$` in a value injecting unit directives or splitting argv | high | mitigate | Task 1: one `_systemd_arg` helper per systemd.service(5) (escape `\`, `"`, double `%`/`$`; refuse Cc control characters, and `$` in the executable path); `--at` refuses Cc; unit text rendered before any write; exec_start_argv round-trip tests over hostile values |
| T-261014-nim-02 | Tampering | pairs.pair_state_paths / schedule unit names / job log path: `../`, `/`, leading dot or dash, non-ASCII names | high | mitigate | Task 1: safe_name_problem fullmatch `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` at pair_add, pair_state_paths, schedule_add/remove, render_service/timer, _rotate_job_log_if_large; gsync loop isolates a bad pair (D-02) |
| T-261014-nim-03 | Tampering | google/cache._download_one, orphan cleanup, prune_cache, gsync.default_cache_dir: media id or album token used as a path (today's orphan cleanup can unlink a file outside the cache) | high | mitigate | Task 2: is_safe_media_id `[A-Za-z0-9_-]{1,255}` checked before any request or path; unsafe ids are per-item failures and are skipped by cleanup and prune; default_cache_dir refuses unsafe tokens by name; victim-file regression test |
| T-261014-nim-04 | Denial of service | gsync manifest drift: one deleted-on-frame photo blocks every future run, the nightly included | medium | mitigate | Task 2: drift still fails this run; --apply evicts exactly the drifted entries via GoogleManifest.save (0600 atomic); the next run re-downloads and re-uploads them; e2e four-run self-heal test |
| T-261014-nim-05 | Information disclosure | Drift and failure messages that echo raw Google ids (capability-like) into logs and emails | medium | mitigate | Task 2: `_gid_label` -> redact_link for safe ids, `(unsafe media id)` otherwise; tests assert the raw id is absent |
| T-261014-nim-06 | Tampering | resolve_album / resolve_frame: substring-first resolution making the exact target ambiguous, or picking the wrong one | medium | mitigate | Task 3: exact case-sensitive -> exact case-insensitive -> substring tiers; ties stay ambiguous with that tier's candidates; D-03 id rule unchanged |
| T-261014-nim-07 | Denial of service | Eviction while the frame listing is transiently partial -> extra re-downloads, possible duplicate upload if the next listing is partial too | low | accept | Eviction touches local state only; the next run re-plans against the then-current frame listing (an already-held md5 is re-recorded with no upload) behind every SAFE gate, the empty-frame-listing gate and the write budget; re-upload is additive, never destructive |
| T-261014-nim-08 | Tampering | `StandardOutput=append:` log path under LOG_DIR containing `%` or whitespace | low | accept | LOG_DIR is the operator's own ~/.local/state; the variable component (job) is validated in Task 1; escaping the path would risk breaking working units |
| T-261014-nim-SC | Tampering | npm/pip/cargo installs | low | accept | No package installs or dependency changes (stdlib `re`, `shlex`, `unicodedata` only; uv.lock untouched) |
</threat_model>

<verification>
- `uv run --extra dev pytest -q -m "not live"` — full offline suite green (current count plus the new tests), zero failures, zero warnings-as-errors.
- `git diff --name-only 9610c30 -- pyproject.toml pushframe/__init__.py CHANGELOG.md uv.lock` prints nothing (no version, release or dependency change).
- `git log --oneline 9610c30..HEAD` shows a test(...) red commit before each fix(...) green commit for all three tasks.
- Manual sanity (optional, no side effects): render a unit for a hostile album into a scratch dir with the pure renderers and run `systemd-analyze verify` against it only if the tool is present. Never install it into ~/.config/systemd/user.
</verification>

<success_criteria>
- Ordinary schedules render byte-identically (pinned). Hostile values round-trip or are refused by name with nothing written.
- Pair and job names obey one rule everywhere a path or unit name is built. `cadre-venus` and `nightly` still work.
- No Google media id or album token can steer a write or unlink outside the cache dir.
- Manifest drift: loud on the run that finds it, named items, self-healed by the next --apply run. Dry-runs never mutate.
- `Cadre` resolves next to `Cadre 2025` for albums and frames alike. Ambiguity is never silently resolved.
- docs/ERRORS.md and docs/CLI.md describe the new messages and rules. No version, CHANGELOG or lock change.
</success_criteria>

<output>
Create `.planning/quick/261014-nim-review-hardening-batch-2-systemd-unit-qu/261014-nim-SUMMARY.md` when done
</output>
