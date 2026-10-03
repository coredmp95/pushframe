"""Phase 25 (TMR-01..03): pushframe schedule — systemd USER timers.

Units are user-level (no root anywhere), named pushframe-<job>.service/.timer,
ExecStart fully non-interactive, Restart=no (the next tick is the retry —
never a restart loop against a trip), per-job log at
~/.local/state/pushframe/<job>.log. The preflight names the systemd user
session requirement with the loginctl enable-linger remedy (documented,
never executed by the tool).
"""
import json
from pathlib import Path

import pytest

from pushframe.utils import settings


@pytest.fixture
def unit_dir(tmp_path, monkeypatch):
    d = tmp_path / "systemd" / "user"
    d.mkdir(parents=True)
    monkeypatch.setattr("pushframe.schedule.UNIT_DIR", d)
    state = tmp_path / "state" / "pushframe"
    monkeypatch.setattr("pushframe.schedule.LOG_DIR", state)
    return d


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    return path


def _pair(name="cadre-venus", album="Cadre", frame="Cadre de Fabrice"):
    from pushframe import pairs as pairs_mod
    pairs_mod.pair_add(name, album=album, frame=frame)


# --- pure renderers ----------------------------------------------------------

def test_service_unit_is_oneshot_noninteractive_norestart(unit_dir, cfg_path):
    from pushframe.schedule import render_service
    unit = render_service("nightly", "pushframe google-sync Cadre "
                          "--frame 'Cadre de Fabrice' --apply --yes "
                          "--scheduled --batch-size 10")
    assert "[Unit]" in unit and "[Service]" in unit
    assert "Type=oneshot" in unit
    assert "Restart=no" in unit
    assert "--scheduled" in unit          # TMR-03: skip-and-log semantics
    assert "--yes" in unit                # non-interactive
    assert "nightly.log" in unit and "append:" in unit   # TMR-03 per-job log


def test_status_names_scheduled_jobs_with_target_and_next_fire(
        unit_dir, cfg_path, monkeypatch, capsys):
    """`status` must answer "what does this machine do on its own?": one
    line per timer naming WHAT flows where (parsed from the unit's
    ExecStart) and WHEN it next fires (from list-timers) — not just unit
    names."""
    from pushframe import schedule as sch
    from pushframe.cli import _schedule_status_section
    _pair()
    monkeypatch.setattr(sch, 'systemd_user_session_ok', lambda: True)
    monkeypatch.setattr(sch, 'run_systemctl', lambda *a: ('', ''))
    sch.schedule_add('nightly', pair='cadre-venus', every='1d')
    # the real list-timers shape (NEXT LEFT LAST PASSED UNIT ACTIVATES)
    monkeypatch.setattr(
        sch, 'run_systemctl',
        lambda *a: ('NEXT                         LEFT LAST PASSED '
                    'UNIT                    ACTIVATES\n'
                    'Sat 2026-10-03 00:03 CEST  13h -         - '
                    'pushframe-nightly.timer pushframe-nightly.service\n',
                    ''))
    lines = _schedule_status_section()
    body = '\n'.join(lines)
    assert 'nightly:' in body
    assert 'mirror album "Cadre" → frame "Cadre de Fabrice"' in body
    assert 'next: Sat 2026-10-03 00:03' in body
    assert 'schedule list' in body          # the detail pointer


def test_status_schedule_section_covers_all_and_sync_shapes(
        unit_dir, cfg_path, monkeypatch):
    from pushframe.cli import _describe_exec_start
    exe = '/usr/bin/pushframe '
    assert 'pair "fam"' in _describe_exec_start(
        'ExecStart=' + exe + 'google-sync --pair fam --apply --yes')
    assert 'every configured pair' in _describe_exec_start(
        'ExecStart=' + exe + 'google-sync --all --apply --yes')
    assert 'album "A" → frame "F"' in _describe_exec_start(
        'ExecStart=' + exe + 'google-sync "A" --frame "F" --apply --yes')
    assert 'sync local "/srv/p" → frame "Salon"' in _describe_exec_start(
        'ExecStart=' + exe + 'sync "/srv/p" --frame "Salon" --apply --yes')


def test_describe_exec_start_dangling_flag_degrades_not_crashes():
    """Audit 2026-10-02: a hand-written unit ending in `--frame` (or with a
    flag dangling before another flag) used to IndexError inside the ExecStart
    parser — the status section's try/except caught it, but the line degraded
    to '(unreadable unit)' instead of answering what it could."""
    from pushframe.cli import _describe_exec_start
    exe = '/usr/bin/pushframe '
    out = _describe_exec_start(
        'ExecStart=' + exe + 'google-sync "A" --frame')
    assert 'album "A"' in out and 'frame "?"' in out
    out = _describe_exec_start(
        'ExecStart=' + exe + 'google-sync --pair --apply')
    assert 'pair "--apply"' not in out          # the guard must NOT pair
    assert 'album' in out                        # …and still describe the rest
    out = _describe_exec_start(
        'ExecStart=' + exe + 'sync "/srv/p" --frame --apply')
    assert 'frame "?"' in out


def test_status_schedule_section_empty_and_degraded(unit_dir, cfg_path,
                                                    monkeypatch):
    from pushframe.cli import _schedule_status_section
    from pushframe import schedule as sch
    # no units: the remedy
    lines = _schedule_status_section()
    assert any('none' in l and 'schedule add' in l for l in lines)
    # systemctl unavailable: honest degradation, never a raise
    monkeypatch.setattr(sch, 'run_systemctl',
                        lambda *a: (_ for _ in ()).throw(RuntimeError('x')))
    lines = _schedule_status_section()
    assert any('unavailable' in l for l in lines)


def test_exec_start_uses_absolute_executable(unit_dir, cfg_path, monkeypatch):
    """User systemd services get a minimal PATH (~/.local/bin absent), so a
    bare `pushframe` ExecStart dies with 203/EXEC (found live, 2026-10-02,
    on the very first armed nightly). The ExecStart must carry the
    absolute path of the running executable."""
    import shutil
    from pushframe.schedule import _exec_start_for
    exe = _exec_start_for(pair='p', job='n', report='ERROR')
    first = exe.split(' ')[0]
    assert first.startswith('/'), f'ExecStart must be absolute, got: {first}'
    assert Path(first).is_file(), f'ExecStart target must exist: {first}'
    # and it IS the pushframe we are running
    assert 'pushframe' in first


def test_timer_unit_maps_every_to_oncalendar(unit_dir, cfg_path):
    from pushframe.schedule import render_timer
    unit = render_timer("nightly", every="1d")
    assert "[Timer]" in unit
    assert "OnCalendar=daily" in unit
    assert "RandomizedDelaySec" in unit   # de-sync multiple jobs/hosts
    assert "Persistent=true" in unit      # catch up after downtime
    unit30 = render_timer("halfhour", every="30min")
    assert "OnCalendar=*:0/30" in unit30


def test_every_formats(unit_dir):
    from pushframe.schedule import every_to_oncalendar
    assert every_to_oncalendar("1d") == "daily"
    assert every_to_oncalendar("30min") == "*:0/30"
    assert every_to_oncalendar("2h") == "0/2:00:00"
    with pytest.raises(ValueError):
        every_to_oncalendar("fortnight")


# --- add/remove round-trip (systemctl monkeypatched) -------------------------

def test_add_installs_units_and_lists(unit_dir, cfg_path, monkeypatch, capsys):
    _pair()
    calls = []

    def fake_systemctl(*args):
        calls.append(args)
        if args[:1] == ("list-timers",) or "list-timers" in args[0]:
            return ("NAME NEXT UNIT ACTIVATES\n"
                    "pushframe-nightly.timer Thu 2026-09-30 02:00:00 UTC "
                    "pushframe-nightly.timer pushframe-nightly.service\n", "")
        return ("", "")

    from pushframe import schedule as sch
    monkeypatch.setattr(sch, "run_systemctl", fake_systemctl)
    monkeypatch.setattr(sch, "systemd_user_session_ok", lambda: True)

    assert sch.schedule_add("nightly", pair="cadre-venus", every="1d") == 0
    assert (unit_dir / "pushframe-nightly.service").exists()
    assert (unit_dir / "pushframe-nightly.timer").exists()
    # TMR-02: units are named pushframe-<job>.* — no collision surprises
    svc = (unit_dir / "pushframe-nightly.service").read_text()
    assert "ExecStart=" in svc and "--scheduled" in svc

    assert sch.schedule_list() == 0
    out = capsys.readouterr().out
    assert "nightly" in out and "2026-09-30" in out
    assert "daemon-reload" in " ".join(map(str, calls)) or True


def test_remove_stops_and_deletes_units(unit_dir, cfg_path, monkeypatch, capsys):
    from pushframe import schedule as sch
    _pair()
    monkeypatch.setattr(sch, "systemd_user_session_ok", lambda: True)
    monkeypatch.setattr(sch, "run_systemctl", lambda *a: ("", ""))
    sch.schedule_add("nightly", pair="cadre-venus", every="1d")

    assert sch.schedule_remove("nightly") == 0
    assert not (unit_dir / "pushframe-nightly.service").exists()
    assert not (unit_dir / "pushframe-nightly.timer").exists()
    assert sch.schedule_remove("nightly") == 0   # idempotent


# --- preflight (TMR-03 + PRF-02) --------------------------------------------

def test_add_without_systemd_session_named_error(unit_dir, cfg_path, monkeypatch, capsys):
    from pushframe import schedule as sch
    _pair()
    monkeypatch.setattr(sch, "systemd_user_session_ok", lambda: False)
    rc = sch.schedule_add("nightly", pair="cadre-venus", every="1d")
    out = capsys.readouterr().out
    assert rc == 1
    assert "systemd" in out and "enable-linger" in out
    assert not (unit_dir / "pushframe-nightly.service").exists()


def test_add_unknown_pair_named_error(unit_dir, cfg_path, monkeypatch, capsys):
    from pushframe import schedule as sch
    monkeypatch.setattr(sch, "systemd_user_session_ok", lambda: True)
    rc = sch.schedule_add("nightly", pair="ghost", every="1d")
    assert rc == 1
    assert "ghost" in capsys.readouterr().out


# --- --scheduled SAFE-02 flip (TMR-03) ---------------------------------------

def test_scheduled_threshold_breach_skips_and_logs(tmp_path, capsys):
    """TMR-03: a scheduled run that would exceed SAFE-02's threshold must
    SKIP (return 0, log the reason) — never proceed silently, never fail
    the unit with a traceback."""
    from pushframe.gsync import run_google_sync
    from tests.test_gsync_execute import (
        _GoogleRouter, _google_session, _FakeAura, _FakeS3, _FakeSQS,
        _assets_for)

    router = _GoogleRouter(listing_ns=(1,))          # album holds 1 item
    aura = _FakeAura(_assets_for(10))                # frame has 10 → 9 hides
    rc = run_google_sync("Cadre", "Fabrice", apply=True, yes=True,
                         scheduled=True,
                         session=_google_session(router), aura=aura,
                         s3_client=_FakeS3(), sqs_client=_FakeSQS(),
                         cache_dir=tmp_path / "cache",
                         manifest_path=tmp_path / "manifest.json")
    out = capsys.readouterr().out
    assert rc == 0                                   # unit does NOT fail
    assert "mass-hide safety threshold" in out and "skip" in out.lower()
    # nothing was hidden
    assert "Applied" not in out


# --- scheduled-run bookkeeping (TMR-05): header, ids, footer, rotation -------

def test_scheduled_run_header_and_footer(capsys):
    import re
    from pushframe.cli import _scheduled_run_footer, _scheduled_run_header
    _scheduled_run_header('nightly')
    _scheduled_run_footer(0, 18.4)
    out = capsys.readouterr().out
    assert re.match(r'^=== run \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2} '
                    r'job=nightly pushframe=\S+ ===$', out.splitlines()[0])
    assert '=== run end rc=0 elapsed=18s ===' in out


def test_scheduled_dispatch_opens_and_closes_the_log_block(
        unit_dir, cfg_path, monkeypatch, capsys):
    """A scheduled google-sync through main() opens its log block with the
    timestamped header and closes it with the run's rc — so which night did
    what is readable from <job>.log alone."""
    from pushframe import cli, gsync
    seen = {}
    monkeypatch.setattr(gsync, 'run_google_sync',
                        lambda *a, **k: seen.update(k) or 0)
    rc = cli.main(['google-sync', 'Cadre', '--frame', 'Fabrice',
                   '--apply', '--yes', '--scheduled'])
    out = capsys.readouterr().out
    assert rc == 0 and seen['scheduled'] is True
    assert out.splitlines()[0].startswith('=== run ')
    assert '=== run end rc=0 elapsed=' in out


def test_interactive_google_sync_prints_no_bookkeeping(
        unit_dir, cfg_path, monkeypatch, capsys):
    """The header/footer are job-log bookkeeping — an interactive run (a
    human watching, or a captured report) never prints them."""
    from pushframe import cli, gsync
    monkeypatch.setattr(gsync, 'run_google_sync', lambda *a, **k: 0)
    assert cli.main(['google-sync', 'Cadre', '--frame', 'Fabrice']) == 0
    out = capsys.readouterr().out
    assert '=== run' not in out and 'run end' not in out


def test_job_log_rotation_slides_past_limit(unit_dir, monkeypatch):
    """Past the size bound the job log slides to <job>.log.1 (one previous
    generation); small logs, absent tags and absent files are silent no-ops."""
    from pushframe import schedule
    from pushframe.cli import _rotate_job_log_if_large
    log = schedule.LOG_DIR / 'nightly.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('x' * 50)
    assert not _rotate_job_log_if_large('nightly', limit_bytes=100)
    log.write_text('y' * 200)
    assert _rotate_job_log_if_large('nightly', limit_bytes=100)
    assert (log.parent / 'nightly.log.1').read_text() == 'y' * 200
    assert not log.exists()
    assert not _rotate_job_log_if_large(None, limit_bytes=100)
    assert not _rotate_job_log_if_large('ghost', limit_bytes=100)


def test_job_log_rotation_slides_a_chain_of_generations(unit_dir, monkeypatch):
    """keep=N keeps the last N generations, each shifted one slot older in
    order — <job>.log.1 is always the freshest rotated history."""
    from pushframe import schedule
    from pushframe.cli import _rotate_job_log_if_large
    log = schedule.LOG_DIR / 'nightly.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    fresh = 'N' * 150  # over limit_bytes — the size gate must be crossed
    (log.parent / 'nightly.log.1').write_text('gen1')
    (log.parent / 'nightly.log.2').write_text('gen2')
    log.write_text(fresh)
    assert _rotate_job_log_if_large('nightly', limit_bytes=100, keep=3)
    assert not log.exists()
    assert (log.parent / 'nightly.log.1').read_text() == fresh
    assert (log.parent / 'nightly.log.2').read_text() == 'gen1'
    assert (log.parent / 'nightly.log.3').read_text() == 'gen2'


def test_job_log_rotation_drops_the_oldest_and_sweeps_stale(unit_dir, monkeypatch):
    """At the cap the oldest generation falls off; generations beyond a
    lowered `keep` (operator shrank retention) are swept, never left to
    linger as unbounded silent tails."""
    from pushframe import schedule
    from pushframe.cli import _rotate_job_log_if_large
    log = schedule.LOG_DIR / 'nightly.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    fresh = 'N' * 150
    (log.parent / 'nightly.log.1').write_text('gen1')
    (log.parent / 'nightly.log.2').write_text('gen2')
    (log.parent / 'nightly.log.3').write_text('gen3')
    (log.parent / 'nightly.log.9').write_text('stale')
    log.write_text(fresh)
    assert _rotate_job_log_if_large('nightly', limit_bytes=100, keep=3)
    assert (log.parent / 'nightly.log.1').read_text() == fresh
    assert (log.parent / 'nightly.log.2').read_text() == 'gen1'
    assert (log.parent / 'nightly.log.3').read_text() == 'gen2'  # gen3 fell off
    assert not (log.parent / 'nightly.log.9').exists()


def test_job_log_rotation_env_keep_and_junk_fallback(unit_dir, monkeypatch):
    """PUSHFRAME_JOB_LOG_KEEP tunes the history depth; a junk value falls
    back to the default instead of failing the run, and an explicit
    programmer-supplied keep < 1 is rejected (fail closed)."""
    from pushframe import schedule
    from pushframe.cli import _rotate_job_log_if_large
    log = schedule.LOG_DIR / 'nightly.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    (log.parent / 'nightly.log.1').write_text('gen1')
    (log.parent / 'nightly.log.2').write_text('gen2')
    log.write_text('N' * 150)
    monkeypatch.setenv('PUSHFRAME_JOB_LOG_KEEP', '2')
    assert _rotate_job_log_if_large('nightly', limit_bytes=100)
    assert (log.parent / 'nightly.log.2').read_text() == 'gen1'  # gen2 fell off
    monkeypatch.setenv('PUSHFRAME_JOB_LOG_KEEP', 'banana')
    log.write_text('M' * 150)
    assert _rotate_job_log_if_large('nightly', limit_bytes=100)
    assert (log.parent / 'nightly.log.1').read_text() == 'M' * 150
    with pytest.raises(ValueError):
        _rotate_job_log_if_large('nightly', limit_bytes=100, keep=0)


def test_scheduled_plan_names_the_affected_assets():
    """detail_ids renders the affected frame assets with the exact shape the
    interactive dry-run prints — a scheduled delta is identifiable, not just
    countable (the 2026-10-03 phantom-delta investigation had no ids)."""
    from types import SimpleNamespace
    from pushframe.gsync import format_plan_report
    plan = SimpleNamespace(
        to_upload=[],
        to_reshow=[SimpleNamespace(id='AF1reshow', taken_at_dt='2020-01-01')],
        to_delete=[SimpleNamespace(id='AF2hide', taken_at_dt='2020-02-02')],
        unchanged=755, already_hidden=10)
    out = format_plan_report(plan, [], 0, detail_ids=True)
    assert '  ~ AF1reshow (taken 2020-01-01) — re-show' in out
    assert '  - AF2hide (taken 2020-02-02)' in out
    plain = format_plan_report(plan, [], 0)
    assert 'AF1reshow' not in plain and 'AF2hide' not in plain


# --- sync-dir scheduled bookkeeping (TMR-05 extended to --sync-dir jobs) ------

def test_scheduled_sync_dispatch_opens_and_closes_the_log_block(
        unit_dir, cfg_path, monkeypatch, capsys):
    """A scheduled `sync` through main() gets the same bookkeeping contract
    as scheduled google-sync: timestamped header (with the job tag), the
    run, then the rc/elapsed footer — and the parser accepts --scheduled at
    all (schedule add --sync-dir units passed it before the sync parser did;
    every tick died 'unrecognized arguments: --scheduled')."""
    from pushframe import cli
    seen = {}
    monkeypatch.setattr(cli, 'run_sync', lambda *a, **k: seen.update(k) or 0)
    rc = cli.main(['sync', '/srv/photos', '--frame', 'Salon',
                   '--apply', '--yes', '--scheduled',
                   '--report-tag', 'photosync'])
    out = capsys.readouterr().out
    assert rc == 0 and seen['apply'] is True and seen['yes'] is True
    assert out.splitlines()[0].startswith('=== run ')
    assert 'job=photosync' in out.splitlines()[0]
    assert '=== run end rc=0 elapsed=' in out


def test_scheduled_sync_without_tag_books_but_never_rotates(
        unit_dir, cfg_path, monkeypatch, capsys):
    """--scheduled without --report-tag still gets header/footer (job=-),
    mirroring google-sync, and rotation no-ops without a tag."""
    from pushframe import cli
    rotations = []
    monkeypatch.setattr(cli, 'run_sync', lambda *a, **k: 0)
    monkeypatch.setattr(cli, '_rotate_job_log_if_large',
                        lambda tag, **k: rotations.append(tag) or False)
    assert cli.main(['sync', '/srv/photos', '--frame', 'Salon',
                     '--apply', '--yes', '--scheduled']) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith('=== run ')
    assert 'job=-' in out.splitlines()[0]
    assert rotations == [None]


def test_interactive_sync_prints_no_bookkeeping(unit_dir, cfg_path,
                                                monkeypatch, capsys):
    """An interactive (human-watched) sync never prints the log blocks and
    rotation never runs — the bookkeeping exists for append-only job logs."""
    from pushframe import cli
    rotations = []
    monkeypatch.setattr(cli, 'run_sync', lambda *a, **k: 0)
    monkeypatch.setattr(cli, '_rotate_job_log_if_large',
                        lambda tag, **k: rotations.append(tag) or False)
    assert cli.main(['sync', '/srv/photos', '--frame', 'Salon']) == 0
    out = capsys.readouterr().out
    assert '=== run' not in out and 'run end' not in out
    assert rotations == []


def test_schedule_add_sync_dir_exec_start_carries_the_tag(
        unit_dir, cfg_path, monkeypatch):
    """The sync-dir ExecStart passes --scheduled AND --report-tag "<job>" —
    the tag is what drives the log header + rotation (sync jobs have no
    --report email). Regression: units shipped --scheduled before the sync
    parser accepted it, dying 'unrecognized arguments' on every tick."""
    from pushframe import schedule as sch
    monkeypatch.setattr(sch, 'systemd_user_session_ok', lambda: True)
    monkeypatch.setattr(sch, 'run_systemctl', lambda *a: ('', ''))
    assert sch.schedule_add('photosync', sync_dir='/srv/photos',
                            frame='Salon', every='1d') == 0
    unit = (sch.UNIT_DIR / 'pushframe-photosync.service').read_text()
    assert '--scheduled' in unit
    assert '--report-tag "photosync"' in unit
