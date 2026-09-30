"""Album → frame mirror engine (phase 18, plans 18-02/18-03).

The plan-computing half is pure by construction (CSE-05, the structural
dry-run rule): `build_demand`, `run_google_sync_plan` and
`format_plan_report` contain no mutating call and no I/O — everything they
need (listing, manifest, staged outcome, frame assets) is passed in.

The mutating half (`run_google_sync`) composes those with v2.0's
`execute_plan` behind the CLI's apply gate: Google-side downloads run on the
plan-01 bounded pool (CSE-01) while every frame write stays inside
execute_plan's synchronous, WriteBudget-paced path — concurrency never
crosses the Aura client seam. Removal is HIDE-ONLY (CSE-06, SAFE-03):
`removal_mode='hide'` is passed unconditionally and no delete tier exists on
the google-sync surface.

The load-bearing rule (CSE-03 / roadmap criterion 3): demand is rebuilt from
the album LISTING plus the MANIFEST — never from a directory walk of the
(pruned) cache. A manifest member whose cache file is gone still asserts its
md5_hash (the manifest MEANS the upload was confirmed, so the frame already
holds the bytes); its demand entry carries a sentinel path that must never
enter `to_upload` — if the frame disagrees, that is drift and fails loud.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from tqdm import tqdm

from pushframe.google.redaction import redact_link
from pushframe.sync import compute_plan

# Suffix of the sentinel demand path for manifest members whose cache file
# was pruned: the md5 is asserted from the manifest, the path is fictional
# and must never be uploaded.
SENTINEL_SUFFIX = ".absent"


# SAFE-02: a plan whose hide count exceeds this share of the frame's
# hash-bearing assets requires an explicit confirmation (env-overridable).
GOOGLE_SYNC_REMOVAL_THRESHOLD = 0.2


def _threshold() -> float:
    # IDN-04: PUSHFRAME_* primary, AURA_* legacy fallback (one release of grace).
    raw = (os.getenv("PUSHFRAME_GOOGLE_SYNC_REMOVAL_THRESHOLD")
           or os.getenv("AURA_GOOGLE_SYNC_REMOVAL_THRESHOLD"))
    try:
        return float(raw) if raw else GOOGLE_SYNC_REMOVAL_THRESHOLD
    except ValueError:
        return GOOGLE_SYNC_REMOVAL_THRESHOLD


def default_cache_dir(album_share_token: str) -> Path:
    """D-03: staging is per-album, keyed by the album's share token —
    `~/.config/pushframe/google-cache/<share_token>/<google_media_id>`."""
    return Path("~/.config/pushframe/google-cache") / album_share_token


class SafeSyncError(RuntimeError):
    """Named mirror-safety abort (SAFE-01) — never a silent plan."""

    @classmethod
    def empty_listing(cls) -> "SafeSyncError":
        return cls(
            "album listing is EMPTY (SAFE-01) — an empty listing cannot be "
            "distinguished from a truncated one and must never be read as "
            "'hide everything on the frame'; refusing to plan"
        )

    @classmethod
    def truncated_listing(cls) -> "SafeSyncError":
        return cls(
            "album listing is NOT exhausted cleanly (SAFE-01) — a truncated "
            "listing would understate demand and mass-hide the difference; "
            "refusing to plan"
        )

    @classmethod
    def empty_frame_listing(cls) -> "SafeSyncError":
        return cls(
            "frame asset listing is EMPTY (SAFE-01) — indistinguishable from "
            "the live-observed get_assets drift (16-LIVE-FINDINGS); verify "
            "the frame's assets and re-run"
        )

    @classmethod
    def manifest_drift(cls) -> "SafeSyncError":
        return cls(
            "manifest claims an upload was confirmed but the frame reports "
            "no matching md5_hash (SAFE-01 drift guard) — the cache was "
            "pruned on that claim; investigate before re-running"
        )


def build_demand(listing, manifest, staged, cache_dir: Path, *,
                 metadata_item_count: int | None = None,
                 ) -> tuple[dict[str, list[Path]], list[tuple[str, str]], int | None]:
    """Rebuild the v2.0 demand map from the album listing + manifest.

    Returns `(demand, failures, videos_skipped)` where `demand` is shaped
    exactly like `scan_directory`'s output (`{md5_hash: [path]}`, one
    logical want per hash) so v2.0's `compute_plan` consumes it unchanged.

    Per listing item:
    - manifest entry exists → demand key = the manifest's md5_hash; the
      demand path is the staged cache file when present, else the sentinel
      `cache_dir/<id>.absent` (pruned — the frame already holds the bytes).
    - no manifest entry, staged → demand key = the staged md5_hash, real path.
    - otherwise (failed/missing download) → excluded from demand and
      collected in `failures` (SAFE-04: never planned from absent bytes).

    This function NEVER walks `cache_dir` — the cache is a staging area, not
    a source of truth (CSE-03).
    """
    from pushframe.google.cache import videos_skipped as _videos_skipped

    demand: dict[str, list[Path]] = {}
    failures: list[tuple[str, str]] = []
    staged_by_id = staged.staged_by_id if staged is not None else {}
    failed_by_id = dict(staged.failed) if staged is not None else {}

    for item in listing.items:
        gid = item["id"]
        entry = manifest.entry_for(gid) if manifest is not None else None
        if entry is not None:
            md5 = entry["md5_hash"]
            s = staged_by_id.get(gid)
            path = Path(s["path"]) if s else Path(cache_dir) / f"{gid}{SENTINEL_SUFFIX}"
            demand[md5] = [path]
        elif gid in staged_by_id:
            s = staged_by_id[gid]
            demand[s["md5_hash"]] = [Path(s["path"])]
        else:
            error = failed_by_id.get(gid) or (
                "item is neither manifest-backed nor staged — not downloaded"
            )
            failures.append((gid, error))

    videos = _videos_skipped(listing, metadata_item_count)
    return demand, failures, videos


def run_google_sync_plan(listing, manifest, staged, cache_dir: Path,
                         frame_assets: list, *,
                         metadata_item_count: int | None = None,
                         skipped_non_image: int = 0,
                         ) -> tuple[object, list[tuple[str, str]], int | None]:
    """The pure plan computation: SAFE-01 gates → demand → v2.0's compute_plan.

    Returns (SyncPlan, failures, videos_skipped). Raises a named
    SafeSyncError — never returns a plan — when the listing or the frame
    asset listing cannot be trusted.
    """
    if not listing.items:
        raise SafeSyncError.empty_listing()
    if getattr(listing, "exhausted_cleanly", None) is not True:
        raise SafeSyncError.truncated_listing()
    if not frame_assets:
        raise SafeSyncError.empty_frame_listing()

    demand, failures, videos = build_demand(
        listing, manifest, staged, cache_dir,
        metadata_item_count=metadata_item_count,
    )
    plan = compute_plan(demand, frame_assets, skipped_non_image=skipped_non_image)

    # Structural enforcement of the sentinel contract: a sentinel path in
    # to_upload means the manifest claimed a confirmed upload the frame
    # disputes — the cache was pruned on that claim, so downloading again is
    # the ONLY honest recovery and that decision is not the plan's to make.
    if any(p.name.endswith(SENTINEL_SUFFIX) for p in plan.to_upload):
        raise SafeSyncError.manifest_drift()

    return plan, failures, videos


def format_plan_report(plan, failures: list[tuple[str, str]],
                       videos_skipped: int | None, *,
                       staged=None) -> str:
    """Render the plan for the CLI (dry-run print and post-apply summary).

    Every Google id passes through `redact_link` — the report is print-ready
    and log-safe by construction.
    """
    lines = [
        f"Plan: {len(plan.to_upload)} to upload, {len(plan.to_reshow)} to "
        f"re-show, {plan.unchanged} unchanged, {len(plan.to_delete)} to hide, "
        f"{plan.already_hidden} already hidden",
    ]
    if videos_skipped is not None:
        lines.append(
            f"Videos skipped: {videos_skipped} (metadata delta — videos are "
            f"out of sync scope, never silently dropped)"
        )
    if failures:
        lines.append(f"Failed downloads ({len(failures)}):")
        for gid, err in failures:
            lines.append(f"  {redact_link(gid)}: {err}")

    staged_by_id = {s["google_media_id"]: s for s in staged.staged} if staged else {}
    if plan.to_upload:
        lines.append("Upload candidates (index | id shape | bytes):")
        for i, p in enumerate(plan.to_upload, 1):
            gid = p.name.removesuffix(SENTINEL_SUFFIX)
            size = staged_by_id.get(gid, {}).get("size_bytes")
            size_s = f"{size:,}" if size is not None else "?"
            lines.append(f"  {i:4d} | {redact_link(gid)} | {size_s}")
    return "\n".join(lines)


def run_google_sync(album_target: str, frame_arg: str, *, apply: bool = False,
                    yes: bool = False, debug: bool = False, session=None,
                    aura=None, s3_client=None, sqs_client=None, budget=None,
                    workers: int = 4, threshold: float | None = None,
                    input_fn=None, is_interactive: bool | None = None,
                    list_shared=None, cache_dir=None, vault_path=None,
                    manifest_path=None, batch_size: int | None = None,
                    pair: str | None = None, run_all: bool = False,
                    scheduled: bool = False) -> int:
    """`pair=` selects a named pair (album+frame from the config store, state
    sharded under the pair name); `run_all=True` runs every configured pair
    with ONE shared budget (MTF-03), recording per-pair outcomes and NEVER
    aborting the loop on one pair's failure (D-02) — exit 1 if any failed.
    `scheduled=True` flips SAFE-02's threshold gate to SKIP-AND-LOG (TMR-03:
    a timed run that would mass-hide reports and stops; it must not proceed
    silently and must not fail the unit)."""
    """The mutating half: album → frame mirror end to end (plan 18-03).

    DI seams mirror run_sync/run_google_album conventions: session/aura/s3/
    sqs/budget injectable for offline tests, `input_fn` replaces input(),
    `is_interactive` replaces the sys.stdin.isatty() check (None = consult
    stdin; tests pass True/False explicitly), `list_shared` overrides album
    resolution, `threshold` wins over the env.

    Flow: resolve album+frame → SAFE-01 prechecks → concurrent downloads
    (CSE-01, Google side only) → plan (the pure half) → print → apply gate
    (SAFE-02 mass-hide threshold + v2.0's y/N, --yes bypasses both,
    non-interactive without --yes fails closed) → execute_plan with
    removal_mode='hide' UNCONDITIONALLY (CSE-06/SAFE-03: no delete tier on
    this verb) → manifest persisted ONLY for progress-confirmed uploads →
    prune → report. Without --apply it returns before any mutating call.

    Exit codes: 0 dry-run/aborted confirmation, 1 failure, 2 usage/ambiguity.
    """
    from pushframe.google.cache import download_to_cache, prune_cache
    from pushframe.google.manifest import GoogleManifest
    from pushframe.google.client import GoogleSession
    from pushframe.google.enumerate import (
        EnumerateError,
        enumerate_album,
        list_shared_albums,
    )
    from pushframe.google.redaction import redact_tokens
    from pushframe.google.vault import CookieVaultError
    from pushframe.cli import _print_album_candidates, resolve_album, resolve_frame

    if input_fn is None:
        input_fn = input
    if is_interactive is None:
        is_interactive = sys.stdin.isatty()

    # --- pair mode (phase 25, MTF-01..03) ----------------------------------
    if run_all or pair is not None:
        from pushframe import pairs as pairs_mod
        if run_all:
            names = list(pairs_mod.all_pairs())
            if not names:
                print('google-sync: no pairs configured — add one with '
                      '`pushframe config pair add <name> --album A --frame F`')
                return 2
        else:
            try:
                pairs_mod.pair_resolve(pair)
            except Exception as e:
                print(f'google-sync: {e}')
                return 2
            names = [pair]

        # D-02: run every pair; one shared budget (MTF-03); exit 1 if any
        # pair failed, 0 only if all OK.
        results = []
        for name in sorted(names):                    # deterministic order
            spec = pairs_mod.pair_resolve(name)
            m_path, c_path = pairs_mod.pair_state_paths(name)
            print(f'=== pair [{name}]: album "{spec["album"]}" → frame '
                  f'"{spec["frame"]}" ===')
            rc = run_google_sync(spec['album'], spec['frame'], apply=apply,
                                 yes=yes, debug=debug, session=session,
                                 aura=aura, s3_client=s3_client,
                                 sqs_client=sqs_client, budget=budget,
                                 workers=workers, threshold=threshold,
                                 input_fn=input_fn,
                                 is_interactive=is_interactive,
                                 list_shared=list_shared,
                                 cache_dir=str(c_path),
                                 manifest_path=m_path,
                                 batch_size=batch_size,
                                 scheduled=scheduled)
            results.append((name, rc))
        failed = [n for n, rc in results if rc != 0]
        print('--- --all report ---')
        for n, rc in results:
            print(f'  [{"ok" if rc == 0 else "FAILED"}] {n}')
        return 0 if not failed else 1
    if pair is None and frame_arg == '--all':
        # handled above only when run_all=True; a literal '--all' frame name
        # without the flag is a user mistake — keep it explicit:
        pass

    if session is None:
        # PRF-01 (phase 24): the vault preflight BEFORE any work — named,
        # with the full remedy (google-link + the headless ssh -X recipe).
        try:
            from pushframe.preflight import require_google_vault
            require_google_vault(vault_path)
        except Exception as e:
            print(f'google-sync failed: {e}')
            return 1
        try:
            # The pinned path flows END-TO-END (2026-09-30 CI lesson: pinning
            # the preflight alone left this read on the default path — on a
            # vault-less machine the run died 'bootstrap first' before ever
            # reaching its intended failure, and a test pinning only the
            # preflight could push the DEFAULT read onto the network).
            session = (GoogleSession.from_vault(path=str(vault_path))
                       if vault_path else GoogleSession.from_vault())
        except CookieVaultError as e:
            print(f'google-sync failed: {e}')
            return 1

    # --- album resolution (D-05 surface, phase 17 conventions) ---
    try:
        albums = list_shared() if list_shared is not None else list_shared_albums(session)
        resolved = resolve_album(album_target, albums)
    except EnumerateError as e:
        print(f'google-sync failed: {redact_tokens(str(e))}')
        return 1

    if resolved.status == 'ambiguous':
        print(f"'{album_target}' matches more than one album — re-run with a "
              f"link/id or a fuller name:")
        _print_album_candidates(resolved.candidates, numbered=True, show_count=True)
        return 2
    if resolved.status == 'not_found':
        print(f"No album matches '{album_target}'. Available shared albums:")
        _print_album_candidates(albums, numbered=True, show_count=True)
        return 2
    album = resolved.album

    page_key = None
    if album.share_url and 'key=' in album.share_url:
        page_key = album.share_url.split('key=', 1)[1].split('&', 1)[0] or None

    try:
        listing = enumerate_album(session, album.album_id, page_key=page_key)
    except EnumerateError as e:
        print(f'google-sync failed: {redact_tokens(str(e))}')
        return 1

    # SAFE-01 prechecks happen BEFORE any frame contact.
    if not listing.items:
        print(f'google-sync failed: {SafeSyncError.empty_listing()}')
        return 1
    if listing.exhausted_cleanly is not True:
        print(f'google-sync failed: {SafeSyncError.truncated_listing()}')
        return 1

    # --- frame resolution + asset listing (v2.0 conventions) ---
    if aura is None:
        from pushframe.aura import Aura
        from pushframe.cli import _configure_cli_logging
        aura = Aura()
        _configure_cli_logging(debug)
        # Phase 24 (SEC-01/02): the ONE session path — stored token first
        # (the scheduled google-sync case), env password override, else the
        # ONE tty prompt. Non-TTY without any credential fails named, never
        # hangs. An INJECTED aura (tests, doctor) manages its own auth —
        # the DI contract says call sites never re-authenticate it.
        from pushframe.session import establish_session, SessionError
        try:
            aura = establish_session(aura=aura)
        except SessionError as e:
            print(f'not authenticated: {e}')
            return 1
        except Exception as e:
            print(f'Login failed: {e}')
            return 1

    from pushframe.session import auto_refresh_stored, frames_read_with_refresh
    _stored = auto_refresh_stored()
    frames = frames_read_with_refresh(
        aura, who=(_stored or {}).get('email'), stored=_stored)
    if frames is None:
        return 2
    frame_res = resolve_frame(frame_arg, frames)
    if frame_res.status == 'ambiguous':
        print(f"'{frame_arg}' matches more than one frame:")
        for i, f in enumerate(frame_res.candidates, 1):
            print(f'  {i}. {f.name} (id: {f.id})')
        return 2
    if frame_res.status == 'not_found':
        print(f"No frame matches '{frame_arg}'. Available frames:")
        for f in frame_res.candidates:
            print(f'  - {f.name} (id: {f.id})')
        return 2
    frame = frame_res.frame

    try:
        frame_assets = aura.get_all_assets(frame.id)
    except Exception as e:
        print(f'google-sync failed: reading frame assets failed: {e}')
        return 1
    if not frame_assets:
        print(f'google-sync failed: {SafeSyncError.empty_frame_listing()}')
        return 1

    # --- downloads (CSE-01: Google side only) + plan (pure half) ---
    manifest = GoogleManifest.load(manifest_path)
    cdir = Path(cache_dir) if cache_dir else default_cache_dir(album.album_id)
    with tqdm(total=len(listing.items), desc='Downloading', unit='photo',
              disable=not sys.stderr.isatty()) as bar:
        def _dl_progress(gid: str, ok: bool) -> None:
            bar.update(1)
            bar.set_postfix_str(f'{redact_link(gid)} {"ok" if ok else "FAIL"}')

        staged = download_to_cache(session, listing, cdir,
                                   manifest=manifest, workers=workers,
                                   progress=_dl_progress)

    try:
        plan, failures, videos = run_google_sync_plan(
            listing, manifest, staged, cdir, frame_assets,
            metadata_item_count=album.item_count,
        )
    except SafeSyncError as e:
        print(f'google-sync failed: {e}')
        return 1

    print(format_plan_report(plan, failures, videos, staged=staged))

    if not apply:
        return 0  # structural dry-run default — nothing above mutated anything

    # ---- every path below is mutating ----

    if not yes and not is_interactive:
        print('--apply requires --yes when running non-interactively')
        return 1

    # SAFE-02: mass-hide gate — removals vs the frame's hash-bearing assets.
    hash_bearing = [a for a in frame_assets if a.md5_hash]
    effective_threshold = threshold if threshold is not None else _threshold()
    removal_count = len(plan.to_delete)
    if removal_count > effective_threshold * max(len(hash_bearing), 1):
        print(f'⚠ {removal_count} of {len(hash_bearing)} photos on '
              f'"{frame.name}" (id: {frame.id}) would be hidden — over the '
              f'{effective_threshold:.0%} safety threshold (SAFE-02).')
        if scheduled:
            # TMR-03: a timed run NEVER proceeds over the threshold — it
            # skips (exit 0 so the unit doesn't fail) and logs the reason;
            # the operator reads the job log and acts deliberately.
            print(f'SKIPPED (--scheduled): SAFE-02 threshold breach — no '
                  f'photos were hidden. Run google-sync manually to review '
                  f'and confirm this plan.')
            return 0
        if not yes:
            answer = input_fn('Proceed with this plan? [y/N] ')
            if answer.strip().lower() not in ('y', 'yes'):
                print('Aborted.')
                return 0

    if not yes:
        answer = input_fn(f'About to apply this plan to "{frame.name}" '
                          f'(id: {frame.id}). Proceed? [y/N] ')
        if answer.strip().lower() not in ('y', 'yes'):
            print('Aborted.')
            return 0

    from pushframe.aws.s3client import S3Client
    from pushframe.aws.sqsclient import SQSClient
    from pushframe.sync import execute_plan, TripDetectedError

    s3 = s3_client if s3_client is not None else S3Client()
    sqs = sqs_client if sqs_client is not None else SQSClient()

    # CSE-01 (MOD-05's rule): frame writes are paced by the per-account
    # WriteBudget. Built at this boundary like run_sync does (execute_plan
    # itself never constructs one); omitted only when the caller injected a
    # budget=None explicitly. Budget state lives per account email (T-09-02).
    import os as _os
    from pushframe.cli import _build_write_budget, _configure_cli_logging
    if debug:
        _configure_cli_logging(True)
    if budget is None and s3_client is None:
        # venus 2026-09-30 (debug gsync-apply-budget-none-crash): resolve the
        # account through session.account_email (env override THEN stored
        # session) — the bare getenv read got None on this token-session host
        # and crashed on email.encode() after the y confirmation.
        from pushframe.session import account_email
        budget = _build_write_budget(account_email(), ignore_budget=False)

    staged_by_id = staged.staged_by_id
    confirmed_paths: list[str] = []

    # Anti-panic, phase 23.5: state the pacing contract BEFORE the bar starts.
    # A budget-limited run can spend minutes between batches (30-request
    # bucket, 0.75/min refill) — an operator who knows that reads a pacing
    # countdown as progress, not as a hang.
    total_items = len(plan.to_upload) + len(plan.to_delete)
    if total_items:
        print(f'applying {total_items} item(s) at write-budget pace — long '
              f'"pacing" countdowns between batches are NORMAL (≈0.75 write '
              f'requests/min refill after the 30-request burst); every wait '
              f'is shown live. Interrupts are safe: progress already confirmed '
              f'is kept.')

    with tqdm(total=len(plan.to_upload) + len(plan.to_delete), desc='Applying',
              unit='item', disable=not sys.stderr.isatty()) as bar:
        def _progress(kind, identifier, ok, error=None):
            bar.update(1)
            name = Path(identifier).name if kind == 'upload' else str(identifier)
            if ok:
                # Venus phase-23.5 fix: name the file — 'upload ok photo-2.jpg'
                # instead of a bare postfix the operator cannot follow.
                bar.set_postfix_str(f'{kind} ok {name}')
            else:
                # Escalate failures WITH the cause + the actionable remedy,
                # instead of a context-free FAIL. 401s during a write run are
                # the account-lockout signature (phase 23.5 venus regression):
                # the remedy names the wait-and-relogin path immediately.
                cause = str(error or '').strip() or 'unknown error'
                short = cause if len(cause) <= 120 else cause[:117] + '...'
                if '401' in short:
                    remedy = ' — likely account lockout: STOP, wait ~30 min, then `pushframe status` to re-login'
                else:
                    remedy = ''
                bar.set_postfix_str(f'{kind} FAIL {name}: {short}{remedy}')

        def _wait(remaining):
            # Venus phase-23.5 fix: execute_plan's on_wait was never wired, so
            # inter-chunk cooldowns AND write-budget waits (the 252 s/item the
            # operator saw) rendered as a frozen bar. Both arrive here; one
            # honest label says pacing is normal and the bar is alive.
            bar.set_postfix_str(f'pacing {remaining:.0f}s — budget refill/cooldown, normal')

        def _progress_factory(inner):
            def cb(kind, identifier, ok):
                inner(kind, identifier, ok)
                if kind == 'upload' and ok:
                    confirmed_paths.append(str(identifier))
            return cb

        def _on_error(kind, identifier, ok, reason):
            # execute_plan's new on_error seam: the failure CAUSE reaches the
            # live bar, not just the summary lines at the end.
            _progress(kind, identifier, ok, error=reason)

        exec_kwargs: dict = {}
        if budget is not None:
            exec_kwargs['budget'] = budget
        # Phase 23.5 (venus): the trip keys on per-batch VOLUME (doctor's
        # 1-item write passed seconds before a 50-item chunk 401'd). A
        # smaller --batch-size stays under the detection threshold. Forwarded
        # ONLY when supplied — execute_plan's own default (50) otherwise
        # rules, preserving the phase-08 no-override contract.
        if batch_size is not None:
            exec_kwargs['batch_size'] = batch_size
        try:
            result = execute_plan(plan, aura, frame.id, s3_client=s3,
                                  sqs_client=sqs, removal_mode='hide',
                                  progress=_progress_factory(_progress),
                                  on_wait=_wait, on_error=_on_error, **exec_kwargs)
        except TripDetectedError as e:
            # Phase 23.5: the trip's proven signature — stopped on the FIRST
            # refusal (no 5-failure iteration). Clean resume next run.
            bar.close()
            print(f'google-sync stopped: {e}')
            print(f'{len(confirmed_paths)} item(s) confirmed written and ARE '
                  f'on the frame; the rest will be attempted next run (the '
                  f'manifest remembers only confirmed writes — nothing gets '
                  f'uploaded twice).')
            print('This is the anti-abuse trip (proven body signature). Wait '
                  '60+ min, then `pushframe doctor` before the next attempt.')
            return 1
        except Exception as e:
            from pushframe.sync import _classify_auth_failure
            bar.close()
            print(f'google-sync failed: apply aborted: {e}')
            print(f'What happened: the run stopped early to protect the account. '
                  f'{len(confirmed_paths)} item(s) were confirmed written before '
                  f'the stop and ARE on the frame — the next run recognizes them '
                  f'(verify probe) and will NOT upload them twice.')
            verdict = _classify_auth_failure(str(e))
            if verdict:
                print(f'Diagnosis (from the 401 server body): {verdict}.')
            else:
                print('About the wait: the anti-abuse trip can be SCOPED to the '
                      'assets surface — `pushframe status` may keep working '
                      '(login/frames reads are a different surface) even while '
                      'writes still 401. Do NOT trust a green status as an '
                      'all-clear: wait 60+ min from the abort before the next '
                      'attempt, and make the retry the ONLY call (extra logins '
                      'can extend a login-scope trip).')
            return 1

    # Persist manifest entries for every staged item whose bytes are PROVEN
    # on the frame — either progress-confirmed via this apply's uploads, or
    # already held (the frame's own listing reports that md5_hash, v2.0's
    # content-hash contract). A manifest entry MEANS "the bytes live on the
    # frame", whichever evidence established it — this is what makes the
    # NEXT run download nothing (steady state, criterion 3).
    frame_md5s = {a.md5_hash for a in frame_assets if a.md5_hash}
    confirmed_md5s: set[str] = set()
    for path_str in confirmed_paths:
        s = staged_by_id.get(Path(path_str).name)
        if s:
            confirmed_md5s.add(s['md5_hash'])
    added = 0
    for s in staged.staged:
        gid = s['google_media_id']
        if manifest.entry_for(gid) is None and (
                s['md5_hash'] in confirmed_md5s or s['md5_hash'] in frame_md5s):
            manifest.add(gid, md5_hash=s['md5_hash'],
                         size_bytes=s['size_bytes'],
                         album_share_token=album.album_id)
            added += 1
    if added or manifest.entries:
        manifest.save(manifest_path)

    print(f'Applied: {result.upload_succeeded} uploaded, '
          f'{result.delete_succeeded} hidden, {result.reshow_succeeded} re-shown')
    if result.upload_failures:
        print(f'{len(result.upload_failures)} upload(s) FAILED — no manifest '
              f'entry written; they will retry next run')
    if failures:
        print(f'{len(failures)} download(s) failed and were NOT synced '
              f'(they will retry next run)')
    pruned = prune_cache(cdir, set(manifest.entries))
    print(f'Cache pruned: {pruned} file(s) removed; '
          f'{len(staged.failed)} failed download(s) kept for retry')
    return 0
