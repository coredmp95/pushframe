"""Album → frame mirror engine (phase 18, plans 18-02/18-03).

The plan-computing half is pure by construction (CSE-05, the structural
dry-run rule): `build_demand`, `run_google_sync_plan`,
`format_plan_report` and `format_noop_message` contain no mutating call and no I/O — everything they
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
enter `to_upload` — if the frame disagrees, that is drift and fails loud
(this run uploads and hides nothing); an --apply run then forgets the drifted
manifest entries so the next run re-downloads them.
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
    from pushframe.google.cache import is_safe_media_id
    if not is_safe_media_id(album_share_token):
        raise SafeSyncError(
            "album share token is not a safe directory name — refusing to "
            "build a cache path from it"
        )
    # B1: expand at call time (HOME honoured like utils/settings.py) — a
    # literal `~` Path is relative and would create a `~` dir under the CWD.
    return Path("~/.config/pushframe/google-cache").expanduser() / album_share_token


def _gid_label(gid: str) -> str:
    """Log-safe label for a Google id: redact_link for a safe id, a fixed
    literal otherwise (redact_link passes non-token strings through raw)."""
    from pushframe.google.cache import is_safe_media_id
    return redact_link(gid) if is_safe_media_id(gid) else "(unsafe media id)"


class SafeSyncError(RuntimeError):
    """Named mirror-safety abort (SAFE-01) — never a silent plan.

    `drifted_ids` is non-empty only for manifest drift: the listing ids whose
    manifest entry the frame no longer holds (B4: --apply forgets them)."""

    drifted_ids: tuple[str, ...] = ()
    # Machine-readable class (wire watch streak): listing:* are Google-side
    # listing gates; frame:* / manifest:* are never counted as wire failures.
    kind: str = "other"

    @classmethod
    def empty_listing(cls) -> "SafeSyncError":
        err = cls(
            "album listing is EMPTY — an empty listing cannot be "
            "distinguished from a truncated one and must never be read as "
            "'hide everything on the frame'; refusing to plan (safety gate)"
        )
        err.kind = "listing:empty"
        return err

    @classmethod
    def truncated_listing(cls) -> "SafeSyncError":
        err = cls(
            "album listing is NOT exhausted cleanly — a truncated "
            "listing would understate demand and mass-hide the difference; "
            "refusing to plan (safety gate) — usually a transient Google "
            "answer: nothing was hidden; re-run later"
        )
        err.kind = "listing:truncated"
        return err

    @classmethod
    def listing_below_metadata(cls, listed: int, expected: int) -> "SafeSyncError":
        from pushframe.google.enumerate import listing_shortfall_tolerance
        tolerance = listing_shortfall_tolerance(expected)
        err = cls(
            f"album metadata says {expected} items, listing returned {listed} "
            f"\u2014 {expected - listed} missing exceeds the {tolerance}-item "
            f"video tolerance: a truncated Google answer, NOT videos; "
            f"refusing to plan (safety gate) \u2014 nothing was hidden; "
            f"re-run later"
        )
        err.kind = "listing:below-metadata"
        return err

    @classmethod
    def empty_frame_listing(cls) -> "SafeSyncError":
        err = cls(
            "frame asset listing is EMPTY — indistinguishable from "
            "the live-observed get_assets drift; verify "
            "the frame's assets and re-run (safety gate)"
        )
        err.kind = "frame:empty-listing"
        return err

    @classmethod
    def manifest_drift(cls, drifted_ids=()) -> "SafeSyncError":
        ids = tuple(drifted_ids)
        labels = ", ".join(_gid_label(g) for g in ids[:10])
        if len(ids) > 10:
            labels += f", +{len(ids) - 10} more"
        plural = "item" if len(ids) == 1 else "items"
        err = cls(
            "manifest claims an upload was confirmed but the frame reports "
            f"no matching md5_hash (state drift) for {len(ids)} {plural}: "
            f"{labels} — usually the photo was deleted from the frame in the "
            "Aura app; nothing was uploaded or hidden this run (safety gate)"
        )
        err.drifted_ids = ids
        err.kind = "manifest:drift"
        return err


def check_listing_trust(listing, metadata_item_count: int | None = None) -> None:
    """The SAFE-01 listing gates, shared by the pure plan and the CLI path.

    Order: empty -> truncated -> below the album metadata count (beyond the
    video tolerance). Raises a named SafeSyncError; returns None when the
    listing may be planned from.
    """
    from pushframe.google.enumerate import listing_shortfall_tolerance

    if not listing.items:
        raise SafeSyncError.empty_listing()
    if getattr(listing, "exhausted_cleanly", None) is not True:
        raise SafeSyncError.truncated_listing()
    if (metadata_item_count is not None
            and metadata_item_count - len(listing.items)
            > listing_shortfall_tolerance(metadata_item_count)):
        raise SafeSyncError.listing_below_metadata(
            len(listing.items), metadata_item_count)


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
    a source of truth (CSE-03). The sentinel path is fictional: it is never
    opened, written or unlinked — only its `.name` suffix marks it.
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
    asset listing cannot be trusted (empty / truncated / below-metadata
    listing via check_listing_trust, or an empty frame listing).
    """
    check_listing_trust(listing, metadata_item_count)
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
    sentinel_uploads = [p for p in plan.to_upload
                        if p.name.endswith(SENTINEL_SUFFIX)]
    if sentinel_uploads:
        # Map back through the demand map (path -> md5), never by parsing
        # ids out of file names; then every listing item whose manifest
        # entry carries a drifted md5, in listing order, de-duplicated.
        path_to_md5 = {p: md5 for md5, paths in demand.items() for p in paths}
        drifted_md5s = {path_to_md5[p] for p in sentinel_uploads
                        if p in path_to_md5}
        drifted_ids: list[str] = []
        for item in listing.items:
            entry = manifest.entry_for(item["id"]) if manifest is not None \
                else None
            if (entry is not None and entry["md5_hash"] in drifted_md5s
                    and item["id"] not in drifted_ids):
                drifted_ids.append(item["id"])
        raise SafeSyncError.manifest_drift(drifted_ids)

    return plan, failures, videos


def format_plan_report(plan, failures: list[tuple[str, str]],
                       videos_skipped: int | None, *,
                       staged=None, detail_ids: bool = False) -> str:
    """Render the plan for the CLI (dry-run print and post-apply summary).

    Every Google id passes through `redact_link` — the report is print-ready
    and log-safe by construction. `detail_ids` names the affected frame
    assets too (same shape the interactive dry-run prints): a scheduled
    run's log must answer WHICH photos a delta touched, not just how many —
    the 2026-10-03 phantom-delta investigation had no ids to work with.
    """
    lines = [
        f"Plan: {len(plan.to_upload)} to upload, {len(plan.to_reshow)} to "
        f"re-show, {plan.unchanged} unchanged, {len(plan.to_delete)} to hide, "
        f"{plan.already_hidden} already hidden",
    ]
    if detail_ids:
        for asset in plan.to_reshow:
            lines.append(f'  ~ {asset.id} (taken {asset.taken_at_dt}) — re-show')
        for asset in plan.to_delete:
            lines.append(f'  - {asset.id} (taken {asset.taken_at_dt})')
    unmanaged = getattr(plan, 'unmanaged_visible', None) or []
    if unmanaged:
        lines.append(
            f"Unmanaged: {len(unmanaged)} visible photo(s) on the frame have "
            f"no content hash and are NOT managed by the mirror (left as-is "
            f"\u2014 never hidden automatically)"
        )
        if detail_ids:
            for asset in unmanaged:
                lines.append(
                    f'  ? {asset.id} (taken {asset.taken_at_dt}) '
                    f'\u2014 no content hash, unmanaged')
    if videos_skipped is not None:
        lines.append(
            f"Videos skipped: {videos_skipped} (metadata delta — videos are "
            f"out of sync scope, never silently dropped)"
        )
    if failures:
        lines.append(f"Failed downloads ({len(failures)}):")
        for gid, err in failures:
            lines.append(f"  {_gid_label(gid)}: {err}")

    staged_by_id = {s["google_media_id"]: s for s in staged.staged} if staged else {}
    if plan.to_upload:
        lines.append("Upload candidates (index | id shape | bytes):")
        for i, p in enumerate(plan.to_upload, 1):
            gid = p.name.removesuffix(SENTINEL_SUFFIX)
            size = staged_by_id.get(gid, {}).get("size_bytes")
            size_s = f"{size:,}" if size is not None else "?"
            lines.append(f"  {i:4d} | {_gid_label(gid)} | {size_s}")
    return "\n".join(lines)


def format_drift_remedy(count: int, *, evicted: bool) -> str:
    """The recovery line printed after a manifest-drift failure. Pure."""
    entries = "entry" if count == 1 else "entries"
    if evicted:
        return (f"Recovery: forgot {count} manifest {entries} for those items "
                f"\u2014 the next run re-downloads them and re-uploads only "
                f"what the frame does not hold (additive: nothing is hidden "
                f"or deleted); re-run google-sync or let the next scheduled "
                f"run heal it")
    return (f"Recovery: re-run with --apply to forget those {count} manifest "
            f"{entries} (this dry-run changed nothing); the run after that "
            f"re-downloads them and re-uploads only what the frame does not "
            f"hold")


def format_noop_message(plan) -> str:
    """The no-op line for a plan with nothing to upload, re-show or hide.

    Pure. When visible hashless frame assets exist (`unmanaged_visible`) the
    mirror cannot vouch for them, so the message must not claim the frame
    already mirrors the album. 'Nothing to do' stays the leading phrase —
    report.py keys the 'OK — nothing to do' subject off it.
    """
    unmanaged = getattr(plan, 'unmanaged_visible', None) or []
    if not unmanaged:
        return (f'Nothing to do \u2014 the frame already mirrors the album '
                f'({plan.unchanged} unchanged, {plan.already_hidden} already hidden).')
    return (f'Nothing to do for the mirror ({plan.unchanged} unchanged, '
            f'{plan.already_hidden} already hidden) \u2014 but {len(unmanaged)} '
            f'visible photo(s) on the frame have no content hash and are NOT '
            f'managed by the mirror, so the frame is not an exact copy of the album.')


def _run_fingerprint(listing, album, albums) -> dict:
    """The run's wire fingerprint: the listing's (inner / cursor / item) plus
    the resolved /albums ds:5 row length bucket, borrowed from the listed
    album with the same id when the resolved one (a share link) lacks it."""
    return _with_albums_shape(getattr(listing, 'fingerprint', None),
                              album, albums)


def _with_albums_shape(fingerprint, album, albums) -> dict:
    fp = dict(fingerprint or {})
    row_shape = getattr(album, 'row_shape', None)
    if row_shape is None:
        row_shape = next(
            (a.row_shape for a in albums or ()
             if getattr(a, 'album_id', None) == getattr(album, 'album_id', None)
             and getattr(a, 'row_shape', None) is not None), None)
    fp['albums'] = row_shape
    return fp


def run_google_sync(album_target: str, frame_arg: str, *, apply: bool = False,
                    yes: bool = False, debug: bool = False, session=None,
                    aura=None, s3_client=None, sqs_client=None, budget=None,
                    workers: int = 4, threshold: float | None = None,
                    input_fn=None, is_interactive: bool | None = None,
                    list_shared=None, cache_dir=None, vault_path=None,
                    manifest_path=None, batch_size: int | None = None,
                    pair: str | None = None, run_all: bool = False,
                    scheduled: bool = False, wire_state_path=None) -> int:
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
    from pushframe.google import wire
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
            # Anti-ping-pong guard (5.1.31): two mirroring pairs that resolve
            # to the SAME frame fight forever — each run sees the other's
            # photos as "on the frame but not in my album" and hides them,
            # the other run re-shows them (and hides back). Hides are
            # reversible so nothing is lost, but the frame never settles and
            # scheduled runs start tripping the mass-hide gate. Refuse up
            # front — before any pair runs — instead of a silent fight.
            frames: dict[str, list[str]] = {}
            for name in names:
                spec = pairs_mod.pair_resolve(name)
                frames.setdefault(str(spec['frame']).casefold(), []).append(name)
            clashing = {f: ns for f, ns in frames.items() if len(ns) > 1}
            if clashing:
                for f, ns in sorted(clashing.items()):
                    print(
                        f'google-sync: pairs {", ".join(sorted(ns))} all '
                        f'mirror the SAME frame ("{f}") — two mirroring '
                        f"sources on one frame hide each other's photos and "
                        f're-show them in turn; the frame never settles and '
                        f'scheduled runs start tripping the mass-hide guard. '
                        f'Remove one pair (`pushframe config pair remove '
                        f'<name>`) or re-point it at another frame, or feed '
                        f'one frame from several sources with `push` '
                        f'(additive) instead.'
                    )
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
            try:
                m_path, c_path = pairs_mod.pair_state_paths(name)
            except pairs_mod.PairError as e:
                # D-02: a hand-edited unsafe pair name fails ITSELF, named;
                # the loop (and the --all report) carries on.
                print(f'google-sync: {e}')
                results.append((name, 1))
                continue
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
                                 scheduled=scheduled,
                                 wire_state_path=wire.state_path_for(pair=name))
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

    # Wire-format watch (quick-261014-tlx): per-pair state file in pair mode
    # (forwarded by the loop above), else keyed by the album target.
    if wire_state_path is None:
        wire_state_path = wire.state_path_for(album_target=album_target)
    watch = wire.WireWatch(wire_state_path)

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
        for line in wire.format_diagnostics(
                pages=[], fingerprint=None, last_good=watch.last_good()):
            print(line)
        for line in watch.on_failure(wire.failure_class(e), None):
            print(line)
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
    metadata_count = getattr(album, 'item_count', None)
    if metadata_count is None:
        # A share-link/id target resolves to an AlbumSummary without metadata;
        # borrow the count from the listed album with the same id so the
        # SAFE-01 below-metadata gate cannot be bypassed.
        metadata_count = next(
            (a.item_count for a in albums
             if getattr(a, 'album_id', None) == album.album_id
             and getattr(a, 'item_count', None) is not None), None)

    page_key = None
    if album.share_url and 'key=' in album.share_url:
        page_key = album.share_url.split('key=', 1)[1].split('&', 1)[0] or None

    seen_photos = [0]

    def _listing_progress(page_number: int, count: int) -> None:
        # Same page lines google-album prints (261018-eh2): a 500+ album
        # spends seconds paging between the Album: header and the first
        # Downloading tick — the operator must see the listing advance
        # (display sweep 261018-evx, review A2).
        seen_photos[0] += count
        print(f'listing page {page_number}: '
              f'{count} photos ({seen_photos[0]} total)', flush=True)

    try:
        listing = enumerate_album(session, album.album_id, page_key=page_key,
                                  expected_count=metadata_count,
                                  progress=_listing_progress)
    except EnumerateError as e:
        print(f'google-sync failed: {redact_tokens(str(e))}')
        for line in wire.format_diagnostics(
                pages=getattr(e, 'pages', []),
                fingerprint=getattr(e, 'fingerprint', None),
                last_good=watch.last_good(), metadata_count=metadata_count):
            print(line)
        for line in watch.on_failure(
                wire.failure_class(e),
                _with_albums_shape(getattr(e, 'fingerprint', None),
                                   album, albums)):
            print(line)
        return 1

    # SAFE-01 prechecks happen BEFORE any frame contact.
    try:
        check_listing_trust(listing, metadata_count)
    except SafeSyncError as e:
        print(f'google-sync failed: {e}')
        if e.kind.startswith('listing:'):
            for line in wire.format_diagnostics(
                    pages=getattr(listing, 'pages', []),
                    fingerprint=_run_fingerprint(listing, album, albums),
                    last_good=watch.last_good(),
                    metadata_count=metadata_count,
                    listed=len(listing.items)):
                print(line)
            for line in watch.on_failure(
                    wire.failure_class(e), _run_fingerprint(listing, album, albums)):
                print(line)
        return 1

    # The listing passed every SAFE-01 gate: fingerprint the Google wire and
    # warn once if it changed since the last good run (advisory — it never
    # changes the exit code).
    for line in watch.on_listing_ok(_run_fingerprint(listing, album, albums)):
        print(line)

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

    from pushframe.cli import _frame_assets_page_printer
    try:
        frame_assets = aura.get_all_assets(frame.id,
                                           progress=_frame_assets_page_printer())
    except Exception as e:
        print(f'google-sync failed: reading frame assets failed: {e}')
        return 1
    if not frame_assets:
        print(f'google-sync failed: {SafeSyncError.empty_frame_listing()}')
        return 1

    # --- downloads (CSE-01: Google side only) + plan (pure half) ---
    manifest = GoogleManifest.load(manifest_path)
    try:
        cdir = Path(cache_dir) if cache_dir else default_cache_dir(album.album_id)
    except SafeSyncError as e:
        print(f'google-sync failed: {e}')
        return 1
    # The bar counts only what can actually download this run — manifest-
    # backed items are skipped entirely (steady state = zero downloads), so
    # a 0/757 bar over the full listing would read like a stalled transfer.
    to_download = sum(1 for item in listing.items
                      if manifest.entry_for(item['id']) is None)
    with tqdm(total=to_download, desc='Downloading', unit='photo',
              disable=not sys.stderr.isatty() or to_download == 0) as bar:
        def _dl_progress(gid: str, ok: bool) -> None:
            bar.update(1)
            bar.set_postfix_str(f'{redact_link(gid)} {"ok" if ok else "FAIL"}')

        staged = download_to_cache(session, listing, cdir,
                                   manifest=manifest, workers=workers,
                                   progress=_dl_progress)

    try:
        plan, failures, videos = run_google_sync_plan(
            listing, manifest, staged, cdir, frame_assets,
            metadata_item_count=metadata_count,
        )
    except SafeSyncError as e:
        print(f'google-sync failed: {e}')
        if e.drifted_ids:
            # B4: local state only, so it runs before any confirmation gate
            # on purpose — the NEXT run still passes every SAFE gate, the
            # y/N gate and the write budget before writing anything, and an
            # already-held md5 is re-recorded without an upload. A dry-run
            # never mutates.
            if apply:
                for gid in e.drifted_ids:
                    manifest.entries.pop(gid, None)
                manifest.save(manifest_path)
            print(format_drift_remedy(len(e.drifted_ids), evicted=apply))
        return 1

    print(format_plan_report(plan, failures, videos, staged=staged,
                             detail_ids=scheduled))

    if not apply:
        return 0  # structural dry-run default — nothing above mutated anything

    # A no-op plan (nothing to upload, re-show, or hide) is a success, not a
    # decision: report it and stop — no y/N, no empty apply bar. This also
    # lets a scheduled non-interactive run end its steady-state night without
    # --yes, since it never reaches the mutating gate below.
    if not (plan.to_upload or plan.to_reshow or plan.to_delete):
        print(format_noop_message(plan))
        # Same already-held persistence as the apply path: an item re-added
        # to the album under a fresh Google id whose bytes the frame already
        # holds gets its manifest entry, so the next run downloads nothing.
        frame_md5s = {a.md5_hash for a in frame_assets if a.md5_hash}
        added = 0
        for s in staged.staged:
            gid = s['google_media_id']
            if manifest.entry_for(gid) is None and s['md5_hash'] in frame_md5s:
                manifest.add(gid, md5_hash=s['md5_hash'],
                             size_bytes=s['size_bytes'],
                             album_share_token=album.album_id)
                added += 1
        if added or manifest.entries:
            manifest.save(manifest_path)
        pruned = prune_cache(cdir, set(manifest.entries))
        print(f'Cache pruned: {pruned} file(s) removed; '
              f'{len(staged.failed)} failed download(s) kept for retry')
        return 0

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
              f'{effective_threshold:.0%} mass-hide safety threshold.')
        if scheduled:
            # TMR-03: a timed run NEVER proceeds over the threshold — it
            # skips (exit 0 so the unit doesn't fail) and logs the reason;
            # the operator reads the job log and acts deliberately.
            print(f'SKIPPED (--scheduled): this plan would hide too many '
                  f'photos (over the mass-hide safety threshold) — no '
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
    from pushframe.sync import AwsCredentialsError, execute_plan, TripDetectedError

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

        def _poll(wait_seconds):
            # Display sweep (261018-evx, review A3): the per-chunk best-effort
            # SQS poll freezes the bar's last postfix for the poll's wait —
            # name it instead of a silent hang. With the bar disabled
            # (non-tty scheduled/log runs) tick as a plain stdout line.
            if bar.disable:
                print(f'SQS poll (frame ack, ≤{wait_seconds:.0f}s)…', flush=True)
            else:
                bar.set_postfix_str(f'SQS poll (frame ack, ≤{wait_seconds:.0f}s)…')

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
                                  on_wait=_wait, on_error=_on_error,
                                  on_poll=_poll, **exec_kwargs)
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
        except AwsCredentialsError as e:
            # Debug session s3-expiredtoken-long-run: the AWS (Cognito) keys
            # behind S3 expired and one refresh + retry could not fix it.
            # NOT the anti-abuse trip -- none of its wait advice applies.
            bar.close()
            print(f'google-sync failed: {e}')
            print(f'{len(confirmed_paths)} item(s) were confirmed written before '
                  f'the stop and ARE on the frame — the next run recognizes them '
                  f'and will NOT upload them twice. No wait is needed: re-run '
                  f'once the cause above is fixed.')
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
    #
    # The md5 persisted is the one VERIFIED ON THE FRAME, not the staged
    # file's: an MPO item (5.1.10) uploads its first frame re-encoded as
    # JPEG, so the frame holds bytes whose md5 differs from the staged
    # container's. execute_plan's `uploaded_md5_by_path` carries the md5 of
    # the bytes ACTUALLY sent, keyed by source path — for a confirmed item
    # that is the identity the frame now reports. Persisting the container
    # md5 would make the next run demand it, find it absent (sentinel), and
    # trip the drift guard.
    frame_md5s = {a.md5_hash for a in frame_assets if a.md5_hash}
    path_to_gid = {s['path']: s['google_media_id'] for s in staged.staged}
    added = 0
    seen_gids: set[str] = set()
    # 1. Confirmed uploads: the md5 of what was actually sent.
    for path_str in confirmed_paths:
        gid = path_to_gid.get(path_str)
        real_md5 = result.uploaded_md5_by_path.get(path_str)
        if gid is None or real_md5 is None or manifest.entry_for(gid):
            continue
        manifest.add(gid, md5_hash=real_md5,
                     size_bytes=staged_by_id[gid]['size_bytes'],
                     album_share_token=album.album_id)
        seen_gids.add(gid)
        added += 1
    # 2. Already-held items: the staged md5 IS on the frame (no reduction
    # happened — these were not uploaded this run).
    for s in staged.staged:
        gid = s['google_media_id']
        if gid in seen_gids or manifest.entry_for(gid) is not None:
            continue
        if s['md5_hash'] in frame_md5s:
            manifest.add(gid, md5_hash=s['md5_hash'],
                         size_bytes=s['size_bytes'],
                         album_share_token=album.album_id)
            added += 1
    if added or manifest.entries:
        manifest.save(manifest_path)

    print(f'Applied: {result.upload_succeeded} uploaded, '
          f'{result.delete_succeeded} hidden, {result.reshow_succeeded} re-shown')
    if result.credential_refreshes:
        print(f'AWS credentials renewed {result.credential_refreshes} time(s) '
              f'mid-run after an expiry (items retried once, nothing lost)')
    if result.upload_failures:
        print(f'{len(result.upload_failures)} upload(s) FAILED — no manifest '
              f'entry written; they will retry next run')
    if result.upload_skipped:
        # Per-item content skips (sync.classify_write_failure): never fed the
        # lockout breaker. Named, because a retry cannot fix them — no
        # manifest entry is written, so every run re-downloads, re-checks and
        # re-reports them until they leave the album (or become supported).
        print(f'{len(result.upload_skipped)} item(s) SKIPPED — content '
              f'pushframe cannot upload (nothing was sent; NOT on the frame; '
              f're-checked and re-reported every run until removed from the '
              f'album):')
        for path_str, reason in result.upload_skipped:
            print(f'  {_gid_label(Path(path_str).name)}: {redact_tokens(reason)}')
    if failures:
        print(f'{len(failures)} download(s) failed and were NOT synced '
              f'(they will retry next run)')
    pruned = prune_cache(cdir, set(manifest.entries))
    print(f'Cache pruned: {pruned} file(s) removed; '
          f'{len(staged.failed)} failed download(s) kept for retry')
    return 0
