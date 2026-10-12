---
phase: quick-261012-qwv
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - pushframe/gsync.py
  - pushframe/sync.py
  - tests/test_gsync_engine.py
  - pushframe/utils/io.py
  - pushframe/google/cache.py
  - pushframe/google/bootstrap.py
  - pushframe/google/manifest.py
  - pushframe/ratelimit.py
  - tests/test_private_paths.py
  - pushframe/config_store.py
  - pushframe/cli.py
  - tests/conftest.py
  - pushframe/report.py
  - tests/test_report.py
autonomous: true
requirements: [B1, B2, S4, S5]
gap_closure: false

estimate:
  tokens: 90000
  raw_tokens: 90000
  tasks: 3
  confidence: low

must_haves:
  truths:
    - "B1: default_cache_dir(token) returns an ABSOLUTE path under the user's home (~/.config/pushframe/google-cache/<token>, expanded at call time) — a scheduled run from any CWD never again creates a literal `~` directory under that CWD."
    - "B1: no `Path(\"~...\")` / `Path('~...')` literal in pushframe/ is used unexpanded — a source-scan test fails on any new one (module-level UPPER_CASE constants whose consumers expand are the only allowed form)."
    - "B2: compute_plan stays pure and records every VISIBLE hashless frame asset in SyncPlan.unmanaged_visible; frame_no_hash, unchanged, to_delete, to_reshow, already_hidden keep their exact existing values and semantics; no hashless asset is ever hidden."
    - "B2: format_plan_report prints `N visible photo(s) on the frame have no content hash and are NOT managed by the mirror` when N>0 (ids + taken date per asset in detail_ids mode), and prints nothing new when N==0."
    - "B2: the no-op message still starts with `Nothing to do` but no longer claims `the frame already mirrors the album` when N>0; when N==0 it is byte-identical to today's message."
    - "S5: under the production umask 0o002, every directory pushframe CREATES for private state (chrome-profile, google cache dir + its new parents, manifest/pairs dirs, budget dir, config dir) is 0700 and every staged photo / budget file it writes is 0600 — for library callers too, not only the CLI."
    - "S5: the `pushframe` console-script entry (cli.main) tightens the process umask to include 0o077 (never loosens a stricter one); the pre-existing default config dir ~/.config/pushframe and the default chrome-profile dir are tightened to 0700 when owned by the user; user-chosen dirs (env-override profile, PUSHFRAME_CONFIG_FILE elsewhere) are never chmod-ed retroactively."
    - "S4: report.send never calls smtp.login nor sends the message over cleartext — a STARTTLS failure on a non-465 port raises (deliver() still catches and returns `email send failed: ...`, never failing the run); STARTTLS and the port-465 SMTP_SSL path both use ssl.create_default_context() (certificate + hostname verified)."
    - "Full offline suite green: `uv run --extra dev pytest -q -m \"not live\"` — the current 639 tests plus the new ones, zero failures, DeprecationWarnings still errors."
  artifacts:
    - path: pushframe/gsync.py
      provides: "default_cache_dir expanded at call time; format_plan_report unmanaged line; pure format_noop_message(plan)"
    - path: pushframe/sync.py
      provides: "SyncPlan.unmanaged_visible (defaulted list, appended last); compute_plan populates it"
    - path: pushframe/utils/io.py
      provides: "ensure_private_dir(path, *, tighten=False) -> Path and write_private_bytes(path, data) — stdlib-only helpers"
    - path: pushframe/report.py
      provides: "send() with verified TLS on both transports and a hard refusal of cleartext"
    - path: tests/test_private_paths.py
      provides: "S5 permission tests (helpers, cache, profile, manifest, budget, config dir, CLI umask)"
  key_links:
    - from: pushframe/gsync.py run_google_sync no-op branch
      to: pushframe/gsync.py format_noop_message
      via: "print(format_noop_message(plan)) replaces the inline f-string — the wording is unit-tested, not just the branch"
    - from: pushframe/gsync.py format_plan_report
      to: pushframe/sync.py SyncPlan.unmanaged_visible
      via: "getattr(plan, 'unmanaged_visible', None) or [] — tests/test_schedule.py passes a SimpleNamespace plan without the field and must stay green"
    - from: pushframe/google/cache.py, pushframe/google/bootstrap.py, pushframe/google/manifest.py, pushframe/ratelimit.py, pushframe/config_store.py
      to: pushframe/utils/io.py ensure_private_dir / write_private_bytes
      via: "every listed mkdir/write site calls the helper — Path.mkdir(parents=True) ignores `mode` for intermediate parents, which is why a bare mode=0o700 is not enough"
    - from: pushframe/cli.py main
      to: os.umask
      via: "tighten-only umask before _main(argv) — main is the sole console-script entry (pyproject `pushframe = pushframe.cli:main`, no __main__.py)"
    - from: tests/conftest.py
      to: os.umask
      via: "autouse fixture restores the process umask after every test — several test files call cli.main() in-process and must not leak 0o077 into the rest of the suite"
---

<objective>
Land four verified findings from the 2026-10-12 code/security review of pushframe, each test-first (red, then green) in the existing tests/ conventions:

- B1 — `default_cache_dir()` returns an unexpanded `~` path (prod on venus created `/home/fabrice/~/.config/pushframe/google-cache`); expand it, and pin the whole package against the defect class with a source-scan guard.
- B2 — a VISIBLE hashless frame asset not in the album stays on the frame silently while the log says "Nothing to do — the frame already mirrors the album". REPORT it (new `SyncPlan.unmanaged_visible`, a report line, an honest no-op message). No automatic hiding.
- S4 — `report.send()` swallows a STARTTLS failure and then logs in over cleartext; it also never verifies certificates. Use `ssl.create_default_context()` on both transports and refuse cleartext outright.
- S5 — private state (logged-in chrome-profile, staged photos keyed by album share tokens, budget files, config dir) follows the default umask (prod: 775/664). Add private-by-construction helpers used at every listed site, a tighten-only umask in the CLI entry, and a cheap retroactive tighten of the two default dirs.

Purpose: an unattended-operable CLI must not leak a Google login or share tokens to other local users, must not mail SMTP credentials in cleartext, and must not tell the operator the mirror is perfect when it is not.
Output: patched modules listed in files_modified, one new test file (tests/test_private_paths.py), new tests in tests/test_gsync_engine.py and tests/test_report.py, an umask-restoring autouse fixture in tests/conftest.py. No version bump, no release, no docs change beyond the report.py module docstring.
</objective>

<execution_context>
@~/.claude/gsd-core/workflows/execute-plan.md
@~/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@./CLAUDE.md

Facts verified by the planner against the 5.1.32 tree (do not re-derive, but do re-read the exact lines before editing):

- pushframe/gsync.py:55-58 `default_cache_dir` — the ONLY unexpanded tilde literal in the package. A grep for `Path("~` / `Path('~` lines lacking `expanduser`, excluding `^[A-Z_]+ = Path(` constant assignments, returns exactly that one line. The three constants (google/manifest.py DEFAULT_MANIFEST_PATH, google/vault.py DEFAULT_VAULT_PATH / LEGACY_VAULT_PATH) are expanded at every use site (manifest.py:77,100; vault.py:60,72,75; preflight.py:24).
- pushframe/gsync.py:179-218 `format_plan_report`; :489-492 the no-op branch printing the "Nothing to do — the frame already mirrors the album (...)" f-string. tests/test_schedule.py:375-391 calls format_plan_report with a `SimpleNamespace` plan that has NO `unmanaged_visible` attribute. tests/test_gsync_execute.py:483,511,533 and pushframe/report.py:149 (`'Nothing to do' in output` → email subject "OK — nothing to do") depend on the "Nothing to do" substring.
- pushframe/sync.py:352-368 `SyncPlan` dataclass (frame_no_hash is the last field); :436-439 the hashless branch of compute_plan. `SyncPlan(...)` is constructed by keyword in pushframe/doctor.py and several tests — a new field must have a default and go last.
- tests/test_gsync_engine.py already imports `compute_plan`, `format_plan_report`, `Asset`, and has `_asset(id_, md5_hash, selected=True)` (sets taken_at) — B1 and B2 tests live there.
- S5 sites: google/bootstrap.py:80 (default profile mkdir) and :82-83 (env-override profile mkdir); google/manifest.py:101 (parent mkdir); google/cache.py:77 (`path.write_bytes(body)` staged photo) and :101 (cache_dir mkdir); ratelimit.py:146-152 (parent mkdir + tmp `write_text` + os.replace); config_store.py:74 (parent mkdir). `DEFAULT_PROFILE_DIR` is evaluated at import (bootstrap.py:45) — tests monkeypatch it (see tests/test_cli_google_link.py:43-55 for the env-var clearing pattern).
- Python's `Path.mkdir(mode=..., parents=True)` applies `mode` to the LEAF only; intermediate parents get the umask default. That is why the helper below walks and creates each missing component itself.
- pushframe/cli.py:2142 `main(argv)` wraps `_main(argv)`; tests/test_docs_hint.py shows the clean seam: `monkeypatch.setattr(cli, '_main', lambda argv=None: 0)` then `cli.main([])`. Test files that call `cli.main` in-process: test_docs_hint, test_version, test_migration, test_logout, test_pairs.
- pushframe/report.py:209-226 `send`; :256-259 `deliver` wraps it in `except Exception as e: return f'email send failed: {e}'`. tests/test_report.py:37-70 `_FakeSMTP` (its `starttls(self)` takes no args) and `_factory(calls, **kw)` whose `make(host, port, timeout)` records `('connect', host, port)` as calls[0] — tests assert `calls[0]` equals that exact 3-tuple, and `calls[-1]` is the send record; no test asserts the `('starttls',)` tuple itself.
- Current offline count: 639 collected (`uv run --extra dev pytest -q -m "not live" --collect-only`). pyproject filterwarnings = error::DeprecationWarning.
- No CLI verb writes photos to a user-chosen output directory (grep of cli.py for dump/export/output_dir is empty) — the blanket CLI umask only affects pushframe's own state, unit files and logs.

Out of scope (do not touch): google/vault.py:94 and migration.py:60 parent mkdirs (both only run under the CLI, so the CLI umask covers them), schedule.py UNIT_DIR (systemd's dir, not ours), the `sync` verb's frame_no_hash print (cli.py:1926), report.py `_PROBLEM_MARKERS` (an unmanaged-visible line must NOT turn every ERROR-level night with a frame video into an alert email), any cleanup of the stray `~` directory on venus (prod host, operator action).
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: B1 expand the google cache dir + B2 report visible hashless assets instead of hiding them</name>
  <files>pushframe/gsync.py, pushframe/sync.py, tests/test_gsync_engine.py</files>
  <read_first>pushframe/gsync.py (lines 1-60, 179-218, 440-495), pushframe/sync.py (lines 350-480), tests/test_gsync_engine.py (lines 1-60, 186-215), tests/test_schedule.py (lines 375-391)</read_first>
  <behavior>
    - B1 test_default_cache_dir_is_absolute_under_home: with HOME monkeypatched to tmp_path, default_cache_dir("TOKEN") == tmp_path/".config"/"pushframe"/"google-cache"/"TOKEN", is_absolute() is True, and "~" is not in str(result). RED today (the path is relative and literal).
    - B1 test_no_unexpanded_tilde_path_literals_in_package: scan every *.py under the package dir (located via Path(__file__).resolve().parent.parent / "pushframe"); skip lines whose stripped text starts with "#"; a line matching the regex Path\(\s*['"]~ is an offender unless it contains "expanduser" or matches ^[A-Z_]+\s*=\s*Path\( (module-level constant whose consumers expand). Assert the offender list (file:line: text) is empty. RED today with exactly gsync.py:58.
    - B2 test_compute_plan_lists_visible_hashless_assets_as_unmanaged: frame = [visible hashless "frame-video-v", hidden hashless "frame-video-h", visible hash-bearing asset matching a local hash]; plan.unmanaged_visible == [the visible hashless asset] (same object), plan.frame_no_hash == 2, plan.unchanged == 1, plan.to_delete == [], plan.to_reshow == [], plan.already_hidden == 0. RED today (no such field).
    - B2 test_format_plan_report_names_unmanaged_visible: a plan with one visible hashless asset → report contains "1 visible photo(s) on the frame have no content hash and are NOT managed by the mirror"; without detail_ids the asset id is absent; with detail_ids=True a line containing the asset id and "(taken " is present. A plan with zero unmanaged produces no "NOT managed" text.
    - B2 test_noop_message_does_not_claim_a_perfect_mirror_when_unmanaged: format_noop_message(plan) with N==0 equals exactly "Nothing to do — the frame already mirrors the album (3 unchanged, 1 already hidden)." (byte-identical to today's print, values from the plan); with N>0 it starts with "Nothing to do", does NOT contain "already mirrors", and contains the count and "NOT managed by the mirror".
  </behavior>
  <action>
RED first: add the five tests above to tests/test_gsync_engine.py (extend its existing `from pushframe.gsync import (...)` block with default_cache_dir and format_noop_message; reuse its `_asset` helper — pass md5_hash=None for hashless). Run them, confirm they fail for the stated reason, commit the red tests.

GREEN, B1 (per finding B1): in pushframe/gsync.py `default_cache_dir`, expand the home at call time — build the base as the tilde literal followed by `.expanduser()`, then join the share token. Call-time expansion honours HOME exactly like pushframe/utils/settings.py's call-time convention. Leave the docstring's D-03 shape description intact.

GREEN, B2 (per finding B2 — REPORT, never hide):
1. pushframe/sync.py `SyncPlan`: append a new field `unmanaged_visible: list = field(default_factory=list)` AFTER `frame_no_hash` (keyword construction elsewhere must keep working). Comment it: visible frame assets with no content hash — the mirror cannot match them to the album, so it never touches them, and the report must say so.
2. pushframe/sync.py `compute_plan`: in the existing hashless branch, keep `frame_no_hash += 1`, and additionally append the asset to a local `unmanaged_visible` list when `asset.selected` is truthy, before the `continue`. Pass it into the returned SyncPlan. Extend the docstring's hashless sentence: visible ones are also listed in `unmanaged_visible` so the caller can report them; the function stays pure and mode-agnostic. Do not change any other counter or the pass-1/pass-2 logic.
3. pushframe/gsync.py `format_plan_report`: read `unmanaged = getattr(plan, 'unmanaged_visible', None) or []` (tests/test_schedule.py passes a SimpleNamespace without the field). After the Plan line and its detail_ids re-show/hide lines, and before the "Videos skipped" line, when unmanaged is non-empty append the line: "Unmanaged: {n} visible photo(s) on the frame have no content hash and are NOT managed by the mirror (left as-is — never hidden automatically)". In detail_ids mode follow it with one line per asset shaped like the existing detail lines: two spaces, "? ", the asset id, " (taken {asset.taken_at_dt})", " — no content hash, unmanaged".
4. pushframe/gsync.py: add a pure `format_noop_message(plan) -> str` next to format_plan_report. N = len of the same getattr-guarded list. N==0 returns exactly today's string: "Nothing to do — the frame already mirrors the album ({unchanged} unchanged, {already_hidden} already hidden)." N>0 returns "Nothing to do for the mirror ({unchanged} unchanged, {already_hidden} already hidden) — but {N} visible photo(s) on the frame have no content hash and are NOT managed by the mirror, so the frame is not an exact copy of the album." Replace the inline f-string print in run_google_sync's no-op branch with print(format_noop_message(plan)); leave that branch's condition and everything after the print unchanged. Add format_noop_message to the module docstring's list of pure plan-half functions.

Keep `report.py` untouched in this task (its "Nothing to do" substring check still matches both wordings).
  </action>
  <verify>
    <automated>uv run --extra dev pytest -q -m "not live" tests/test_gsync_engine.py tests/test_sync_engine.py tests/test_schedule.py tests/test_gsync_execute.py tests/test_gsync_all.py tests/test_report.py</automated>
  </verify>
  <acceptance_criteria>
    - The five new tests exist in tests/test_gsync_engine.py and pass; they were committed red first.
    - `grep -n "format_noop_message(plan)" pushframe/gsync.py` shows the call inside run_google_sync.
    - `grep -n "unmanaged_visible" pushframe/sync.py` shows the dataclass field after frame_no_hash and its population in compute_plan.
    - Existing tests in the verify command stay green unmodified (test_schedule.py's SimpleNamespace plan included).
  </acceptance_criteria>
  <done>default_cache_dir returns an absolute home-rooted path; the package has a guard against unexpanded tilde literals; a visible hashless frame asset is listed in SyncPlan.unmanaged_visible, named in the plan report, and the no-op message no longer claims a perfect mirror — with zero change to hiding behaviour or existing counters.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: S5 private-by-construction helpers wired into the Google-side state and the write budget</name>
  <files>pushframe/utils/io.py, pushframe/google/cache.py, pushframe/google/bootstrap.py, pushframe/google/manifest.py, pushframe/ratelimit.py, tests/test_private_paths.py</files>
  <read_first>pushframe/utils/io.py, pushframe/google/cache.py (lines 19-110), pushframe/google/bootstrap.py (lines 17-84), pushframe/google/manifest.py (lines 95-112), pushframe/ratelimit.py (lines 135-155), tests/test_cli_google_link.py (lines 43-56), tests/test_ratelimit.py (lines 183-192)</read_first>
  <behavior>
    All tests run under a module-level fixture `prod_umask` that sets os.umask(0o002) (the venus production umask that produced 775/664) and restores the previous value — the tests prove EXPLICIT modes beat a permissive umask. Modes are compared with stat.S_IMODE.
    - ensure_private_dir creates a fully missing chain tmp/a/b/c with EACH new component 0o700; the pre-existing tmp_path keeps its mode; it returns the Path; a "~"-prefixed input is expanded (HOME monkeypatched to tmp_path).
    - ensure_private_dir on a pre-existing 0o775 leaf leaves it 0o775 by default, and makes it 0o700 with tighten=True.
    - write_private_bytes creates a new file 0o600 with the exact bytes; rewriting a pre-existing 0o664 file leaves it 0o600 with the new bytes.
    - download_to_cache into a missing tmp/"gc"/"AF1Qip...TOKEN" chain: both new dirs 0o700, the staged photo 0o600 (inline fake: a SimpleNamespace session whose `http` is httpx.Client(transport=httpx.MockTransport(...)) returning 200 with body and Content-Length; a SimpleNamespace listing with one item dict having "id" and "base_url" — the shape tests/test_google_cache.py uses).
    - bootstrap._require_profile with no profile env vars and DEFAULT_PROFILE_DIR monkeypatched to a missing tmp/"cfg"/"chrome-profile": both dirs 0o700. With DEFAULT_PROFILE_DIR pre-created 0o775: tightened to 0o700. With PUSHFRAME_PROBE_CHROME_PROFILE pointing at a pre-existing 0o775 dir: left 0o775 (user-chosen, never chmod-ed); pointing at a missing dir: created 0o700.
    - GoogleManifest().save(tmp/"pairs"/"cadre"/"manifest.json") with the dirs missing: "pairs" and "cadre" are 0o700, the file 0o600.
    - WriteBudget(...).save() to tmp/"state"/"budget-x.json" (missing parent): parent 0o700, file 0o600; a pre-existing 0o664 budget file is 0o600 after save, content still round-trips through WriteBudget.load.
  </behavior>
  <action>
RED first: create tests/test_private_paths.py with the tests above (module docstring: S5 of the 2026-10-12 review — private state must be private by construction, not by umask luck; prod showed 775/664). Confirm red (helpers missing, modes 775/664), commit.

GREEN (per finding S5), in pushframe/utils/io.py (stdlib only; add `from pathlib import Path` and `import stat` as needed):
1. `ensure_private_dir(path, *, tighten: bool = False) -> Path` — expanduser the input; walk up from the path collecting every component that does not exist; create them top-down with mkdir(mode=0o700, exist_ok=True) and then os.chmod each NEWLY created one to exactly 0o700 (the mkdir mode is masked by the umask; the chmod makes it deterministic). Never chmod a component that already existed — except the leaf when tighten=True AND it is owned by os.getuid() AND its mode has any group/other bit, in which case chmod it to 0o700. Docstring: why the helper exists (Path.mkdir(parents=True) ignores mode for intermediate parents) and that tighten is reserved for pushframe's own default dirs.
2. `write_private_bytes(path, data: bytes) -> None` — os.open with O_WRONLY|O_CREAT|O_TRUNC and 0o600, os.fchmod(fd, 0o600) (a pre-existing file keeps its old mode under O_CREAT), then write all bytes through os.fdopen(fd, 'wb'). Same pattern as google/manifest.py save.

Wire the call sites (replace only the named mkdir/write; behaviour otherwise unchanged):
3. pushframe/google/cache.py: in download_to_cache, `cache_dir = ensure_private_dir(cache_dir)` replaces the Path() + mkdir pair; in _download_one, the staged photo write uses write_private_bytes(path, body).
4. pushframe/google/bootstrap.py: the default branch calls ensure_private_dir(DEFAULT_PROFILE_DIR, tighten=True) (it holds a logged-in Google browser profile — always ours); the env-override branch calls ensure_private_dir(path) WITHOUT tighten (user-chosen dir, never retro-chmod-ed). Keep the returned values identical.
5. pushframe/google/manifest.py save: ensure_private_dir(p.parent) replaces the parent mkdir (covers the pairs/<name>/ dirs); the 0600 file write stays as is.
6. pushframe/ratelimit.py WriteBudget.save: ensure_private_dir(self.path.parent) replaces the parent mkdir; the tmp file is written with write_private_bytes(tmp_path, payload encoded utf-8) before the unchanged os.replace — the final budget file therefore lands 0600. Update the save docstring with one line: written 0600 (S5).

Import the helpers with full package paths (`from pushframe.utils.io import ensure_private_dir, write_private_bytes`) per CLAUDE.md import conventions.
  </action>
  <verify>
    <automated>uv run --extra dev pytest -q -m "not live" tests/test_private_paths.py tests/test_google_cache.py tests/test_google_manifest.py tests/test_cli_google_link.py tests/test_ratelimit.py tests/test_gsync_execute.py tests/test_gsync_engine.py</automated>
  </verify>
  <acceptance_criteria>
    - tests/test_private_paths.py exists, its tests were committed red first, and they pass.
    - `grep -n "ensure_private_dir" pushframe/google/cache.py pushframe/google/bootstrap.py pushframe/google/manifest.py pushframe/ratelimit.py` lists at least one call in each of the four files (two in bootstrap.py).
    - `grep -n "write_private_bytes" pushframe/google/cache.py pushframe/ratelimit.py` lists one call in each.
    - All pre-existing tests in the verify command stay green unmodified.
  </acceptance_criteria>
  <done>Every listed Google-side and budget directory/file is created 0700/0600 regardless of umask, the default chrome-profile is tightened if it already exists loose, and user-chosen override dirs are never chmod-ed.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: S5 config dir + CLI umask guard, and S4 verified TLS with no cleartext fallback</name>
  <files>pushframe/config_store.py, pushframe/cli.py, tests/conftest.py, tests/test_private_paths.py, pushframe/report.py, tests/test_report.py</files>
  <read_first>pushframe/config_store.py (lines 1-95), pushframe/cli.py (lines 1-40, 2140-2147), tests/conftest.py (lines 1-60), tests/test_docs_hint.py, pushframe/report.py (lines 1-40, 205-260), tests/test_report.py (lines 1-90, 225-275, 480-490)</read_first>
  <behavior>
    S5 (append to tests/test_private_paths.py, same prod_umask fixture):
    - config_store.save with HOME monkeypatched to tmp_path, settings.CONFIG_PATH monkeypatched to tmp_path/".config"/"pushframe"/"config.json", and that directory pre-created 0o775: after save the dir is 0o700 and the file 0o600.
    - config_store.save to a NON-default location tmp_path/"elsewhere"/"config.json" with "elsewhere" pre-created 0o775: the dir stays 0o775 (user-chosen), the file is 0o600; to a missing non-default parent chain: created 0o700.
    - cli.main tightens the umask: with cli._main monkeypatched to `lambda argv=None: 0`, os.umask(0o002) set, cli.main([]) returns 0 and the process umask afterwards is 0o077 (read it with the set-and-restore idiom). Starting from 0o177, it stays 0o177 (tighten-only, never loosens).
    S4 (append to tests/test_report.py):
    - test_send_refuses_cleartext_when_starttls_fails: cfg port 587 with smtp_user/smtp_password; a fake whose starttls raises smtplib.SMTPNotSupportedError; send raises an smtplib.SMTPException whose message mentions STARTTLS and cleartext; the calls list has NO login and NO send entry.
    - test_deliver_starttls_failure_is_a_reason_never_a_crash: same fake through deliver() → returns a string starting "email send failed:" that mentions STARTTLS; nothing raised.
    - test_send_starttls_uses_a_verified_default_context: the context passed to starttls is an ssl.SSLContext with verify_mode == ssl.CERT_REQUIRED and check_hostname True.
    - test_send_port_465_passes_a_verified_context_to_smtp_ssl: port 465 → the factory receives a verified ssl.SSLContext as `context`, no starttls call happens, login and send happen.
  </behavior>
  <action>
RED first: add the S5 tests to tests/test_private_paths.py and the S4 tests to tests/test_report.py. For the S4 fakes, extend tests/test_report.py's `_FakeSMTP.__init__` with `fail_starttls=False`; make `starttls(self, context=None)` raise smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server.") when fail_starttls, else append ("starttls", context); make `_factory`'s inner `make(host, port, timeout, context=None)` keep appending ("connect", host, port) FIRST (calls[0] is asserted as that exact 3-tuple) and then, only when context is not None, append ("ssl_context", context). Add `import smtplib` and `import ssl`. Confirm the new tests fail for the right reason, commit red.

GREEN, tests/conftest.py: add an autouse function-scoped fixture `_restore_umask` that reads the current umask with the set-and-restore idiom, yields, and restores it — cli.main() is called in-process by several test files and must not leak 0o077 into later tests. Comment why.

GREEN, S5 (per finding S5):
1. pushframe/cli.py: add a private `_tighten_umask()` that sets the umask to (current | 0o077) — read via os.umask(0o077) then set os.umask(current | 0o077) — so a stricter operator umask is never loosened. Call it as the FIRST statement of `main(argv)`, before `_main(argv)`. Comment: blanket guard for everything the CLI creates (config, chrome-profile, caches, unit files, logs) — the explicit per-site modes from the helpers cover library callers.
2. pushframe/config_store.py save: replace the parent mkdir with `ensure_private_dir(path.parent, tighten=...)` where tighten is True only when path.parent (expanduser + resolve) equals Path('~/.config/pushframe').expanduser().resolve() computed at call time — the default config dir is pushframe's own and holds the token, google cache, manifests, budget and chrome-profile; a dir the operator chose via PUSHFRAME_CONFIG_FILE is never retro-chmod-ed. Import ensure_private_dir from pushframe.utils.io. The 0600 temp-file write and final chmod stay as is.

GREEN, S4 (per finding S4), pushframe/report.py send:
3. `import ssl`. Build one `ctx = ssl.create_default_context()` per send. Port 465: connect with the (injected or SMTP_SSL) factory passing `timeout=30, context=ctx`. Other ports: connect with `timeout=30` (no context kwarg), then call `smtp.starttls(context=ctx)`; on smtplib.SMTPException raise a new smtplib.SMTPException chained from the original whose message names the host:port, says STARTTLS failed, says the report and the SMTP login are NOT sent over cleartext, and gives the remedy (a relay that offers STARTTLS, or port 465 for implicit SSL). This raise happens before any login or send_message, regardless of whether credentials are configured (the DEBUG-level body carries a log tail — cleartext is refused for the body too). An ssl.SSLError (certificate/hostname failure) propagates unchanged. Keep `deliver()` untouched — its existing `except Exception` turns any of these into the "email send failed: ..." reason, so a report failure never fails the run.
4. Update the module docstring's Transport sentence: STARTTLS is required on non-465 ports (a relay without it is refused, never cleartext), SMTP_SSL on 465, both with certificate and hostname verification.
  </action>
  <verify>
    <automated>uv run --extra dev pytest -q -m "not live"</automated>
  </verify>
  <acceptance_criteria>
    - New S5 tests in tests/test_private_paths.py and the four S4 tests in tests/test_report.py exist, were committed red first, and pass.
    - `grep -n "_tighten_umask()" pushframe/cli.py` shows the call inside main before _main.
    - `grep -n "create_default_context" pushframe/report.py` shows the context built in send; `grep -n "starttls(context=" pushframe/report.py` shows the verified call.
    - The full offline suite passes: 639 pre-existing tests plus all new ones, 0 failed, no warnings promoted to errors.
  </acceptance_criteria>
  <done>The CLI process umask always includes 0o077, the default config dir is 0700 after the next config save, the report mailer never sends credentials or report bodies over cleartext and verifies the server certificate on both transports, and the whole offline suite is green.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| other local users → pushframe state on disk | config token, Google cookie vault, logged-in chrome-profile, staged photos and cache dirs named by album share tokens, budget files |
| pushframe → SMTP relay (network) | SMTP credentials and the report body (DEBUG level carries a log tail) cross an untrusted network |
| frame state → operator's view | the run report is the operator's only view of what the mirror did and did not manage |
| process CWD → filesystem | a relative `~` path lands wherever the scheduled run happened to start |

## STRIDE Threat Register

| Threat ID | Category | Component | Severity | Disposition | Mitigation Plan |
|-----------|----------|-----------|----------|-------------|-----------------|
| T-261012-01 | Information disclosure | google/bootstrap.py chrome-profile, google/cache.py staging, google/manifest.py pairs dirs, ratelimit.py budget file | high | mitigate | Task 2: ensure_private_dir (0700 on every newly created component) + write_private_bytes (0600) at each site; default chrome-profile retro-tightened to 0700 when user-owned |
| T-261012-02 | Information disclosure | ~/.config/pushframe (config dir, 775 in prod) and everything the CLI creates | high | mitigate | Task 3: tighten-only umask (current or 0o077) in cli.main; config_store.save tightens the DEFAULT config dir to 0700 (user-owned only) |
| T-261012-03 | Information disclosure | report.send STARTTLS failure → login over cleartext | high | mitigate | Task 3: SMTPException on STARTTLS failure is re-raised before login/send_message; deliver() keeps catching so the run never fails |
| T-261012-04 | Spoofing | report.send TLS without certificate verification (smtplib's default starttls context does not verify) | high | mitigate | Task 3: ssl.create_default_context() passed to starttls and to SMTP_SSL — CERT_REQUIRED + check_hostname, asserted by tests |
| T-261012-05 | Repudiation | gsync no-op message claims a perfect mirror while visible hashless assets stay unmanaged | medium | mitigate | Task 1: SyncPlan.unmanaged_visible + report line + honest format_noop_message; no automatic hiding (no new destructive path) |
| T-261012-06 | Information disclosure | gsync.default_cache_dir unexpanded `~` → staging dirs named by share tokens under an arbitrary CWD | medium | mitigate | Task 1: call-time expanduser + package-wide source-scan test against unexpanded tilde literals |
| T-261012-07 | Denial of service | retroactive chmod of an operator-chosen shared dir breaking other consumers | low | mitigate | tighten only pushframe's own default dirs (config dir, default chrome-profile) and only when owned by os.getuid(); override dirs never chmod-ed (tested) |
| T-261012-SC | Tampering | npm/pip/cargo installs | high | accept | no package installs or dependency changes in this plan (stdlib ssl/stat/os only) — gate not triggered |
</threat_model>

<verification>
- `uv run --extra dev pytest -q -m "not live"` — 0 failed; collected count = 639 + the new tests (expect roughly 655-665).
- Each finding has at least one test that was committed RED before its fix: B1 and B2 in tests/test_gsync_engine.py, S5 in tests/test_private_paths.py, S4 in tests/test_report.py.
- No version bump: `git diff --stat` touches neither pyproject.toml nor pushframe/__init__.py.
</verification>

<success_criteria>
- B1: default_cache_dir is absolute and home-rooted; the tilde-literal guard test passes over the whole package.
- B2: visible hashless frame assets are reported (count always, ids + taken date in detail_ids mode), the no-op message is honest, compute_plan stays pure, existing counters unchanged, nothing hidden automatically.
- S4: no login or message is ever sent over cleartext; both transports verify certificates; deliver() still never fails the run.
- S5: private state is 0700/0600 under the production umask for library and CLI callers; the CLI umask is tighten-only; only pushframe's own default dirs are retro-tightened.
- Full offline suite green with DeprecationWarnings as errors; no release or version change.
</success_criteria>

<output>
Create `.planning/quick/261012-qwv-review-quick-fixes-cache-dir-expanduser-/261012-qwv-SUMMARY.md` when done. Mention in it that the stray literal directory created on venus by B1 (`/home/fabrice/~/.config/pushframe/google-cache`) is an operator cleanup on the prod host, not done by this plan, and that existing loose subdirectories under a now-0700 config dir are protected by the parent but were not individually chmod-ed.
</output>
