"""Email run reports for scheduled jobs (TMR-04, 2026-10-02 request).

Three operator levels, chosen per job with `schedule add --report LEVEL`:

- DEBUG  email on EVERY run with the full run output plus the pushframe log
         tail (last 100 lines of logs/file_*.log) — for "something is not
         quite right, I need precise traces".
- INFO   email on EVERY run, the run summary (plan, applied, prune, the
         per-pair sections of a --all run).
- ERROR  email ONLY when the run failed or carries a potential problem:
         non-zero exit, upload/download failures, a scheduled mass-hide
         skip, an aborted run.

Transport: plain SMTP (STARTTLS when offered; SMTP_SSL on port 465)
through stdlib smtplib. Configured once with
`pushframe schedule report --to ADDR --smtp-host HOST` — stored in the
0600 config file's `report` key; PUSHFRAME_SMTP_* / PUSHFRAME_REPORT_TO
environment variables override at use time.

A report failure NEVER fails the run: deliver() returns an error string
the caller prints as a warning on stderr.
"""
from __future__ import annotations

import os
import smtplib
import socket
import time
from email.message import EmailMessage
from pathlib import Path

LEVELS = ('DEBUG', 'INFO', 'ERROR')

# Strings OUR OWN run output uses to flag a problem on a zero exit — one
# entry per real message, named after the gsync.py line that prints it
# (tests/test_report.py pins one test per marker; keep both in sync with
# gsync.py):
_PROBLEM_MARKERS = (
    # 'google-sync failed: …' / 'Login failed: …' /
    # 'google-sync failed: apply aborted: …' (all exit 1 — broad net in
    # case a future path forgets to propagate the failure)
    'failed:',
    # '{n} upload(s) FAILED — no manifest entry written; they will retry next run'
    'upload(s) FAILED',
    # '--- --all report ---' then '  [FAILED] pair-name' (--all exits 1;
    # the marker keeps the email working even if that contract drifts)
    '[FAILED]',
    # '{n} download(s) failed and were NOT synced (they will retry next run)'
    'download(s) failed',
    # TMR-03: 'SKIPPED (--scheduled): this plan would hide too many photos
    # (over the mass-hide safety threshold) — no photos were hidden.' —
    # exit 0 BY DESIGN (the unit must not fail over a safety decision),
    # but the operator must hear about it
    'SKIPPED (--scheduled)',
    # 'google-sync stopped: {e}' — the anti-abuse trip (gsync exits 1;
    # marker is the safety net)
    'google-sync stopped:',
    # '⚠ {n} of {m} photos on "{frame}" … would be hidden — over the
    # {t}% mass-hide safety threshold.' — printed when an over-threshold
    # plan IS applied (interactive y, or --yes): a deliberate but
    # potentially destructive action worth an ERROR email
    '⚠',
    # 'Aborted: {error}' — the RateLimitError abort. 'Aborted.' PLAIN
    # (period, no colon) is a deliberate operator decline at a
    # confirmation: NOT a problem — and it does not contain this marker.
    'Aborted:',
)

_USAGE = (
    'usage: pushframe schedule report [--show]\n'
    '       pushframe schedule report --to ADDR --smtp-host HOST '
    '[--smtp-port N] [--smtp-user U] [--smtp-password P] [--from ADDR]\n'
    '       pushframe schedule report --test [--level INFO]\n'
    '       pushframe schedule report --disable\n'
    '\n'
    'Configure where scheduled runs email their report. Levels (chosen per\n'
    'job with `schedule add ... --report LEVEL`):\n'
    '  DEBUG  every run, full run output + log tail (precise traces)\n'
    '  INFO   every run, the summary (frames, photos, actions)\n'
    '  ERROR  only when the run failed or carries a potential problem\n'
    '\n'
    'The SMTP password can be given with --smtp-password (shell history!)\n'
    'or typed hidden at the prompt, or left to PUSHFRAME_SMTP_PASSWORD.\n'
)


def normalize_level(raw: str | None) -> str | None:
    """DEBUG/INFO/ERROR (case-insensitive) or None when unknown."""
    if raw is None:
        return None
    up = raw.strip().upper()
    return up if up in LEVELS else None


def load_config() -> dict:
    """The effective report config: env overrides the stored `report` key."""
    from pushframe import config_store
    stored = config_store.load().get('report', {}) or {}

    def _int(raw, default):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    user = os.getenv('PUSHFRAME_SMTP_USER') or stored.get('smtp_user') or ''
    return {
        'to': os.getenv('PUSHFRAME_REPORT_TO') or stored.get('to'),
        'smtp_host': os.getenv('PUSHFRAME_SMTP_HOST') or stored.get('smtp_host'),
        'smtp_port': _int(os.getenv('PUSHFRAME_SMTP_PORT')
                          or stored.get('smtp_port'), 587),
        'smtp_user': user,
        'smtp_password': (os.getenv('PUSHFRAME_SMTP_PASSWORD')
                          or stored.get('smtp_password')),
        'from': os.getenv('PUSHFRAME_SMTP_FROM') or stored.get('from') or user,
    }


def should_send(level: str, rc: int, output: str) -> bool:
    """DEBUG/INFO always; ERROR only on a failure or a problem marker."""
    if level == 'ERROR':
        return rc != 0 or any(m in output for m in _PROBLEM_MARKERS)
    return True


def _first_line(output: str, prefixes: tuple[str, ...]) -> str:
    for line in output.splitlines():
        for prefix in prefixes:
            if line.startswith(prefix):
                return line[len(prefix):].strip() if ':' in prefix \
                    else line.strip()
    return ''


def build_subject(tag: str | None, level: str, rc: int, output: str) -> str:
    prefix = f'[pushframe] {tag}: ' if tag else '[pushframe] '
    if rc != 0 or level == 'ERROR':
        status = 'FAILED' if rc != 0 else 'ATTENTION'
        detail = (_first_line(output, ('google-sync failed:', 'failed:',
                                       'SKIPPED (--scheduled):'))
                  or f'exit {rc}')
        return f'{prefix}{status} — {detail}'
    applied = _first_line(output, ('Applied:',))
    if applied:
        return f'{prefix}OK — {applied}'
    if 'Nothing to do' in output:
        return f'{prefix}OK — nothing to do'
    return f'{prefix}OK — run finished (exit 0)'


def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f'{m}:{s:02d}' if m else f'{s}s'


def build_body(level: str, *, command: str, rc: int, started_ts: float,
               duration_s: float, output: str,
               log_tail: str | None = None) -> str:
    from pushframe import __version__
    lines = [
        f'pushframe {level} report',
        f'Command:  {command}',
        f'Started:  {time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(started_ts))} UTC',
        f'Duration: {_fmt_duration(duration_s)}',
        f'Result:   {"FAILED" if rc else "OK"} (exit {rc})',
        '',
        '--- run output ---',
        output.rstrip(),
    ]
    if log_tail:
        lines += ['', '--- log tail (last 100 lines) ---', log_tail.rstrip()]
    lines += [
        '', '--',
        f'Sent by pushframe {__version__} on {socket.gethostname()} '
        f'(report level {level}; docs: '
        'https://github.com/coredmp95/pushframe/blob/master/docs/ERRORS.md)',
    ]
    return '\n'.join(lines) + '\n'


def tail_log(*, lines: int = 100, since: float | None = None) -> str | None:
    """The run's own file sink (logs/file_*.log), last `lines` lines.

    The newest file whose mtime is not older than `since` (minus a small
    skew) wins; None when the directory or a matching file is absent.
    """
    log_dir = Path('logs')
    if not log_dir.is_dir():
        return None
    try:
        candidates = sorted(log_dir.glob('file_*.log'),
                            key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for path in candidates:
        try:
            if since is not None and path.stat().st_mtime < since - 5:
                continue
            text = path.read_text(errors='replace')
        except OSError:
            continue
        return '\n'.join(text.splitlines()[-lines:])
    return None


def send(cfg: dict, subject: str, body: str, *, smtp_factory=None) -> None:
    """One email. Raises on failure (deliver() catches — never the run)."""
    factory = smtp_factory or (
        smtplib.SMTP_SSL if int(cfg['smtp_port']) == 465 else smtplib.SMTP)
    with factory(cfg['smtp_host'], cfg['smtp_port'], timeout=30) as smtp:
        if int(cfg['smtp_port']) != 465:
            try:
                smtp.starttls()
            except smtplib.SMTPException:
                pass  # relay offers no STARTTLS — proceed (or fail at login)
        if cfg.get('smtp_user') and cfg.get('smtp_password'):
            smtp.login(cfg['smtp_user'], cfg['smtp_password'])
        msg = EmailMessage()
        msg['From'] = cfg.get('from') or cfg['smtp_user']
        msg['To'] = cfg['to']
        msg['Subject'] = subject
        msg.set_content(body)
        smtp.send_message(msg)


def deliver(level, *, command: str, rc: int, output: str, duration_s: float,
            started_ts: float | None = None, tag: str | None = None,
            to: str | None = None, smtp_factory=None) -> str | None:
    """Gate → build → send. None on success or a gated skip, else the
    reason the email did not go out (the caller prints it as a warning)."""
    lvl = normalize_level(level)
    if lvl is None:
        return f'invalid report level {level!r} (use DEBUG, INFO or ERROR)'
    if not should_send(lvl, rc, output):
        return None
    cfg = load_config()
    recipient = to or cfg.get('to')
    missing = []
    if not recipient:
        missing.append('recipient (`schedule report --to` or --report-to)')
    if not cfg.get('smtp_host'):
        missing.append('SMTP host (`schedule report --smtp-host`)')
    if missing:
        return 'not configured: ' + ', '.join(missing)
    cfg['to'] = recipient
    if not cfg.get('from'):
        cfg['from'] = f'pushframe@{socket.gethostname()}'
    subject = build_subject(tag, lvl, rc, output)
    body = build_body(lvl, command=command, rc=rc,
                      started_ts=started_ts if started_ts is not None else time.time(),
                      duration_s=duration_s, output=output,
                      log_tail=tail_log() if lvl == 'DEBUG' else None)
    try:
        send(cfg, subject, body, smtp_factory=smtp_factory)
    except Exception as e:
        return f'email send failed: {e}'
    return None


# --- `pushframe schedule report` configurator -------------------------------

_SET_FLAGS = ('--to', '--smtp-host', '--smtp-port', '--smtp-user',
              '--smtp-password', '--from')


def configure(args: list[str], *, stdin_isatty: bool | None = None,
              smtp_factory=None) -> int:
    """`pushframe schedule report` — show / set / test / disable."""
    import sys as _sys
    import getpass
    from pushframe import config_store
    is_tty = _sys.stdin.isatty() if stdin_isatty is None else stdin_isatty

    opts: dict[str, str] = {}
    flags: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in _SET_FLAGS:
            if i + 1 >= len(args):
                print(f'schedule report: {arg} needs a value')
                return 2
            opts[arg] = args[i + 1]
            i += 2
        elif arg in ('--show', '--disable', '--test'):
            flags.append(arg)
            i += 1
        elif arg == '--level':
            if i + 1 >= len(args):
                print('schedule report: --level needs a value')
                return 2
            opts['--level'] = args[i + 1]
            i += 2
        else:
            print(f'schedule report: unknown argument {arg!r}')
            print(_USAGE)
            return 2

    if flags.count('--disable') + flags.count('--test') + flags.count('--show') > 1:
        print('schedule report: --show / --test / --disable are exclusive')
        return 2

    if '--disable' in flags:
        data = config_store.load()
        if not data.get('report'):
            print('report config: already disabled (nothing stored).')
            return 0
        data.pop('report', None)
        config_store.save(data)
        print('report config removed — scheduled jobs stop emailing.')
        return 0

    if not opts and '--test' not in flags:
        return _show_config()

    updates = {k.lstrip('-').replace('-', '_'): v for k, v in opts.items()
               if k in _SET_FLAGS}
    if updates:
        data = config_store.load()
        stored = data.get('report', {}) or {}
        stored.update(updates)
        if ('smtp_user' in updates or 'smtp_host' in updates) \
                and not stored.get('smtp_password') \
                and not os.getenv('PUSHFRAME_SMTP_PASSWORD') \
                and '--test' not in flags:
            if is_tty:
                stored['smtp_password'] = getpass.getpass(
                    'SMTP password (input hidden, empty to skip): ')
            else:
                print('note: no SMTP password stored — set one with '
                      '--smtp-password or PUSHFRAME_SMTP_PASSWORD if your '
                      'relay requires login.')
        data['report'] = stored
        config_store.save(data)
        _show_config()
        print('next: `pushframe schedule report --test` sends a trial email; '
              '`schedule add ... --report DEBUG|INFO|ERROR` turns it on per job.')
        return 0

    if '--test' in flags:
        lvl = normalize_level(opts.get('--level')) or 'INFO'
        cfg = load_config()
        missing = [k for k in ('to', 'smtp_host') if not cfg.get(k)]
        if missing:
            print(f'cannot test: not configured ({", ".join(missing)}). Set with '
                  f'`pushframe schedule report --to ADDR --smtp-host HOST`.')
            return 1
        output = ('Plan: 0 to upload, 0 to re-show, 757 unchanged, 0 to hide, '
                  '8 already hidden\nNothing to do — the frame already '
                  'mirrors the album (757 unchanged, 8 already hidden).')
        err = deliver(lvl, command='pushframe google-sync (test report)',
                      rc=0, output=output, duration_s=0.0,
                      tag='test', started_ts=time.time(),
                      smtp_factory=smtp_factory)
        if err:
            print(f'test email NOT sent: {err}')
            return 1
        print(f'test email sent to {cfg["to"]} (level {lvl}) — check the inbox.')
        return 0
    return 0


def _show_config() -> int:
    cfg = load_config()
    configured = bool(cfg.get('to') and cfg.get('smtp_host'))
    print(f'report config: {"configured" if configured else "NOT configured"}')
    print(f'  to:         {cfg.get("to") or "(unset)"}')
    print(f'  smtp host:  {cfg.get("smtp_host") or "(unset)"}')
    print(f'  smtp port:  {cfg.get("smtp_port")}')
    print(f'  smtp user:  {cfg.get("smtp_user") or "(none — relay without login)"}')
    print(f'  smtp pass:  {"*** (stored)" if cfg.get("smtp_password") else "(unset)"}')
    print(f'  from:       {cfg.get("from") or "(defaults to smtp_user)"}')
    if not configured:
        print('set with: pushframe schedule report --to ADDR --smtp-host HOST '
              '[--smtp-port 587] [--smtp-user U]')
    return 0
