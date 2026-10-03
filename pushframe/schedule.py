"""Phase 25 (TMR-01..03): pushframe schedule — systemd USER timers.

No root anywhere: units live in ~/.config/systemd/user/ as
pushframe-<job>.service/.timer. ExecStart is fully non-interactive (the
phase-24 session contract guarantees no prompts; --scheduled flips SAFE-02
to skip-and-log). Restart=no on purpose — the next timer tick is the
retry; a restart loop against an anti-abuse trip would feed the trip.

The unit renderers are PURE (string in, string out) so tests assert
content; the systemctl wrappers are the only impurity and are
monkeypatched in tests.
"""
import subprocess
from pathlib import Path

# Patched by tests (real paths: ~/.config/systemd/user, ~/.local/state/pushframe)
UNIT_DIR = Path('~/.config/systemd/user').expanduser()
LOG_DIR = Path('~/.local/state/pushframe').expanduser()


class ScheduleError(Exception):
    pass


def systemd_user_session_ok() -> bool:
    """True when `systemctl --user` answers (a user bus exists — a real
    session or lingering). The preflight for schedule add."""
    try:
        r = subprocess.run(['systemctl', '--user', 'is-system-running'],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0 or 'degraded' in r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_systemctl(*args) -> tuple[str, str]:
    """The ONLY systemctl invocation point (tests monkeypatch this)."""
    r = subprocess.run(['systemctl', '--user', *args],
                       capture_output=True, text=True, timeout=20)
    return r.stdout, r.stderr


def every_to_oncalendar(every: str) -> str:
    """--every spelling → systemd OnCalendar. Supported: Nmin / Nh / Nd."""
    s = every.strip().lower()
    if s.endswith('min'):
        n = int(s[:-3])
        if n in (60,):
            return 'hourly'
        if 60 % n == 0 and n < 60:
            return f'*:0/{n}'
        raise ValueError(f'cannot map "{every}" to OnCalendar — use --at with '
                         f'an explicit OnCalendar for odd intervals')
    if s.endswith('h'):
        n = int(s[:-1])
        return 'hourly' if n == 1 else f'0/{n}:00:00'
    if s.endswith('d'):
        n = int(s[:-1])
        if n == 1:
            return 'daily'
        raise ValueError(f'cannot map "{every}" to OnCalendar — use --at with '
                         f'an explicit OnCalendar (e.g. --at "Mon *-*-* 02:00")')
    raise ValueError(f'unknown --every "{every}" — use Nmin/Nh/Nd or --at')


def render_service(job: str, exec_start: str) -> str:
    """The service unit: oneshot, no restart (the next tick is the retry),
    per-job append log with a Type=oneshot-compatible shape."""
    log = LOG_DIR / f'{job}.log'
    return f"""[Unit]
Description=pushframe scheduled job '{job}'
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={exec_start}
Restart=no
StandardOutput=append:{log}
StandardError=append:{log}
"""


def render_timer(job: str, every: str | None = None, at: str | None = None) -> str:
    """The timer unit: calendar-based, randomized to de-sync jobs/hosts,
    Persistent to catch up after downtime."""
    if at:
        calendar = at
    elif every:
        calendar = every_to_oncalendar(every)
    else:
        raise ScheduleError('schedule needs --every Nmin|Nh|Nd or --at "OnCalendar"')
    return f"""[Unit]
Description=pushframe timer for job '{job}'

[Timer]
OnCalendar={calendar}
RandomizedDelaySec=15m
Persistent=true
Unit=pushframe-{job}.service

[Install]
WantedBy=timers.target
"""


def _pushframe_command() -> str:
    """Absolute path to the running pushframe executable, for systemd units.

    User systemd services get a minimal PATH (~/.local/bin absent), so a
    bare `pushframe` ExecStart dies with 203/EXEC on the most common
    install (uv tool install). shutil.which finds our OWN executable —
    sys.argv[0]'s directory is the fallback for exotic launches.
    """
    import shutil
    import sys
    found = shutil.which('pushframe')
    if found:
        return found
    argv0 = Path(sys.argv[0]).resolve()
    if argv0.name == 'pushframe' and argv0.is_file():
        return str(argv0)
    return 'pushframe'  # last resort: let systemd's PATH try


def _exec_start_for(pair: str | None = None, album: str | None = None,
                   frame: str | None = None, sync_dir: str | None = None,
                   batch_size: int | None = None, job: str | None = None,
                   report: str | None = None,
                   report_to: str | None = None) -> str:
    """Non-interactive ExecStart from STORED config only (TMR-03)."""
    from pushframe import config_store
    parts = [_pushframe_command()]
    if sync_dir:
        # --report-tag carries the job identity into the run's log header
        # and its rotation (sync jobs have no --report email; the tag is
        # what makes <job>.log self-describing).
        parts += ['sync', f'"{sync_dir}"', f'--frame "{frame}"',
                  '--apply', '--yes', '--scheduled',
                  '--report-tag', f'"{job}"']
    else:
        spec = (config_store.load().get('pairs', {}) or {}).get(pair or '', {})
        album = spec.get('album', album)
        frame = spec.get('frame', frame)
        parts += ['google-sync', f'"{album}"', f'--frame "{frame}"',
                  '--apply', '--yes', '--scheduled']
        if spec.get('batch_size') or batch_size:
            parts += ['--batch-size', str(spec.get('batch_size') or batch_size)]
        if report:
            parts += ['--report', report]
            if job:
                parts += ['--report-tag', f'"{job}"']
            if report_to:
                parts += ['--report-to', f'"{report_to}"']
    return ' '.join(parts)


def schedule_add(job: str, *, pair: str | None = None,
                 album: str | None = None, frame: str | None = None,
                 sync_dir: str | None = None, every: str | None = None,
                 at: str | None = None,
                 batch_size: int | None = None,
                 report: str | None = None,
                 report_to: str | None = None) -> int:
    if not job.replace('-', '').isalnum():
        print(f'schedule: job name "{job}" must be alphanumeric/dashes')
        return 1
    report_level = None
    if report is not None:
        from pushframe import report as report_mod
        report_level = report_mod.normalize_level(report)
        if report_level is None:
            print(f'schedule: --report {report!r} is not a level — use '
                  f'DEBUG, INFO or ERROR (DEBUG: every run with traces; '
                  f'INFO: every run, summary; ERROR: only on a problem)')
            return 1
        if sync_dir:
            print('schedule: --report is wired for google-sync jobs — use '
                  '--pair (or --album/--frame), not --sync-dir')
            return 1
        cfg = report_mod.load_config()
        if not (cfg.get('to') and cfg.get('smtp_host')):
            print('schedule: WARNING — --report is on but no email transport '
                  'is configured; the job will run silently. Set it once: '
                  '`pushframe schedule report --to ADDR --smtp-host HOST` '
                  '(then `schedule report --test`).')
    if not systemd_user_session_ok():
        print('schedule: no systemd USER session available (systemctl '
              '--user does not answer). Remedy: log in once on the machine, '
              'or enable lingering so timers run headless: '
              '`loginctl enable-linger $USER` (documented in docs/CLI.md; '
              'the tool never runs it for you).')
        return 1
    try:
        if pair:
            from pushframe import pairs as pairs_mod
            pairs_mod.pair_resolve(pair)          # named error if unknown
        timer = render_timer(job, every=every, at=at)
    except (Exception,) as e:
        print(f'schedule: {e}')
        return 1

    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    exec_start = _exec_start_for(pair=pair, album=album, frame=frame,
                                 sync_dir=sync_dir, batch_size=batch_size,
                                 job=job, report=report_level,
                                 report_to=report_to)
    (UNIT_DIR / f'pushframe-{job}.service').write_text(
        render_service(job, exec_start))
    (UNIT_DIR / f'pushframe-{job}.timer').write_text(timer)
    run_systemctl('daemon-reload')
    run_systemctl('enable', '--now', f'pushframe-{job}.timer')
    print(f'installed: pushframe-{job}.service/.timer (user units) — '
          f'log: {LOG_DIR}/{job}.log')
    print('headless host? enable lingering so the timer runs without an '
          'active session: loginctl enable-linger $USER')
    return 0


def schedule_list() -> int:
    out, _ = run_systemctl('list-timers', '--all',
                           'pushframe-*.timer', '--no-pager')
    print(out if out.strip() else 'no pushframe timers installed.')
    return 0


def schedule_remove(job: str) -> int:
    svc = UNIT_DIR / f'pushframe-{job}.service'
    tmr = UNIT_DIR / f'pushframe-{job}.timer'
    if not tmr.exists() and not svc.exists():
        print(f'no pushframe timer named "{job}".')
        return 0
    run_systemctl('stop', f'pushframe-{job}.timer')
    run_systemctl('disable', f'pushframe-{job}.timer')
    tmr.unlink(missing_ok=True)
    svc.unlink(missing_ok=True)
    run_systemctl('daemon-reload')
    print(f'removed pushframe-{job}.service/.timer (pair state untouched).')
    return 0
