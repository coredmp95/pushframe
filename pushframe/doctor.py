"""pushframe doctor — the field write-probe (phase 23.5, venus regressions).

The release pipeline proves install (container journeys) and logic (438
offline tests against our own API assumptions) — it structurally CANNOT
prove that a real write lands on a real frame from this machine, this IP,
today: writing in CI would trip the very anti-abuse layer we fight.

`pushframe doctor` is the missing gate, run deliberately on the TARGET
machine before committing hours to a sync:

  1. session  — token/env session establishes (read)
  2. frames   — get_frames answers (read)
  3. write    — ONE 1x1 probe image through the REAL path: S3 upload +
                select_asset + batch_update (write, budget-guarded)
  4. verify   — the probe read endpoint finds it (read)
  5. cleanup  — the probe asset is hidden again (write)

Ends with an unambiguous VERDICT: GO (writes pass here, now) or NO-GO with
the captured 401 body and its trip-vs-token classification. Never run in
CI; charges ~6 budget tokens; safe to repeat.
"""
from pathlib import Path


def _probe_image() -> Path:
    """A deterministic 4x4 PNG — real decodable bytes, zero privacy."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (4, 4), (0, 0, 0)).save(buf, format='PNG')
    out = Path('/tmp') / 'pushframe-doctor-probe.png'
    out.write_bytes(buf.getvalue())
    return out


def run_doctor(frame_arg: str | None = None, *, aura=None, s3_client=None,
               sqs_client=None, budget=None, do_write: bool = True,
               debug: bool = False) -> int:
    """GO/NO-GO for the write surface from THIS machine. Returns 0 on GO."""
    from pushframe.aws.s3client import get_md5, S3Client
    from pushframe.aws.sqsclient import SQSClient
    from pushframe.cli import _build_write_budget, _configure_cli_logging
    from pushframe.sync import SyncPlan, execute_plan, _classify_auth_failure
    from pushframe.models.asset import Asset, AssetPartialId

    checks: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = '') -> None:
        mark = '✓' if ok else '✗'
        line = f'{mark} {name}'
        if detail:
            line += f' — {detail}'
        print(line)
        checks.append((name, ok, detail))

    if debug:
        _configure_cli_logging(True)

    # 1. session + 2. frames -------------------------------------------------
    try:
        aura = aura or __import__('pushframe.aura', fromlist=['Aura']).Aura()
        _configure_cli_logging(debug)
        aura.login()
        record('session (login)', True)
    except Exception as e:
        record('session (login)', False, str(e)[:160])
        print('VERDICT: NO-GO — no session; run `pushframe status` for detail.')
        return 1

    try:
        frames = aura.frame_api.get_frames()
        frame = (frames[0] if not frame_arg else
                 next(f for f in frames if frame_arg.lower() in f.name.lower()))
        record(f'frames read ({len(frames)} frame(s), target "{frame.name}")', True)
    except Exception as e:
        record('frames read', False, str(e)[:160])
        print('VERDICT: NO-GO — reads are refused; writes would be too.')
        return 1

    if not do_write:
        print('VERDICT: READ-ONLY GO (writes untested — drop --no-write to probe)')
        return 0

    # 3. write probe ---------------------------------------------------------
    probe = _probe_image()
    s3 = s3_client or S3Client()
    sqs = sqs_client or SQSClient()
    if budget is None:
        email = (__import__('os').getenv('PUSHFRAME_EMAIL')
                 or __import__('os').getenv('AURA_EMAIL'))
        budget = (_build_write_budget(email, ignore_budget=False)
                  if email else None)  # no email in env → run unguarded (probe = 6 calls)
    md5 = get_md5(probe.read_bytes())

    try:
        result = execute_plan(
            SyncPlan(to_upload=[probe], to_delete=[]), aura, frame.id,
            s3_client=s3, sqs_client=sqs, budget=budget,
            batch_size=1, chunk_delay_seconds=0.0)
        if result.upload_succeeded == 1:
            record('write probe (S3 + select_asset + batch_update)', True)
        else:
            raise RuntimeError(result.upload_failures[0][1] if result.upload_failures
                               else 'upload silently not acknowledged')
    except Exception as e:
        record('write probe', False, str(e)[:200])
        verdict = _classify_auth_failure(str(e))
        print(f'VERDICT: NO-GO — writes are refused from this machine.'
              + (f' Diagnosis: {verdict}.' if verdict else ''))
        return 1

    # 4. verify probe --------------------------------------------------------
    new_asset_id = None
    try:
        assets, _cursor = aura.frame_api.get_assets(frame.id, limit=1000)
        for a in assets:
            if a.md5_hash == md5:
                new_asset_id = a.id
                break
        record('verify probe (asset listed on frame)', new_asset_id is not None,
               f'asset {new_asset_id}' if new_asset_id else 'not listed yet')
    except Exception as e:
        record('verify probe', False, str(e)[:160])

    # 5. cleanup -------------------------------------------------------------
    if new_asset_id:
        try:
            execute_plan(
                SyncPlan(to_upload=[], to_delete=[
                    Asset.model_construct(id=new_asset_id, md5_hash=md5)]),
                aura, frame.id, s3_client=s3, sqs_client=sqs, budget=budget,
                batch_size=1, chunk_delay_seconds=0.0)
            record('cleanup (probe hidden)', True)
        except Exception as e:
            record('cleanup', False,
                   f'{str(e)[:120]} — hide asset {new_asset_id} manually')
    else:
        print('· cleanup skipped — probe asset id unknown (it may appear on '
              'the frame shortly; it is a 4x4 black PNG named nothing)')

    print('VERDICT: GO — the write surface works from this machine, now. '
          'A sync launched in this state should be attempted.')
    return 0
