"""Email run reports (TMR-04, 2026-10-02 request).

Three operator levels with a precise contract:
- DEBUG  every run, full run output + pushframe log tail (precise traces)
- INFO   every run, the summary (frames, photos, actions)
- ERROR  only when the run failed or carries a potential problem
         (non-zero exit, upload/download failures, scheduled mass-hide skip)

A report failure NEVER fails the run. Transport is plain SMTP (stdlib);
the SMTP socket is faked in-process, nothing touches a network.
"""
import json
from pathlib import Path

import pytest

from pushframe.utils import settings


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    return path


@pytest.fixture
def unit_dir(tmp_path, monkeypatch):
    d = tmp_path / "systemd" / "user"
    d.mkdir(parents=True)
    monkeypatch.setattr("pushframe.schedule.UNIT_DIR", d)
    state = tmp_path / "state" / "pushframe"
    monkeypatch.setattr("pushframe.schedule.LOG_DIR", state)
    return d


class _FakeSMTP:
    """Records one session; the factory returns it directly (no network)."""

    def __init__(self, calls, fail_login=False, fail_send=False):
        self._calls = calls
        self._fail_login = fail_login
        self._fail_send = fail_send
        self.entered = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self._calls.append(('starttls',))

    def login(self, user, password):
        if self._fail_login:
            raise RuntimeError('535 bad credentials')
        self._calls.append(('login', user))

    def send_message(self, msg):
        if self._fail_send:
            raise RuntimeError('connection dropped')
        self._calls.append(('send', msg['From'], msg['To'],
                            msg['Subject'], msg.get_content()))


def _factory(calls, **kw):
    def make(host, port, timeout):
        calls.append(('connect', host, port))
        return _FakeSMTP(calls, **kw)
    return make


def _clean_env(monkeypatch):
    for var in ('PUSHFRAME_SMTP_HOST', 'PUSHFRAME_SMTP_PORT',
                'PUSHFRAME_SMTP_USER', 'PUSHFRAME_SMTP_PASSWORD',
                'PUSHFRAME_SMTP_FROM', 'PUSHFRAME_REPORT_TO'):
        monkeypatch.delenv(var, raising=False)


OK_OUT = ('Plan: 0 to upload, 0 to re-show, 757 unchanged, 0 to hide, '
          '8 already hidden\nNothing to do — the frame already mirrors '
          'the album (757 unchanged, 8 already hidden).')
FAIL_OUT = 'google-sync failed: reading frame assets failed: 401'
HIDE_OUT = ('Applied: 111 uploaded, 2 hidden, 0 re-shown')
TRIP_OUT = ('SKIPPED (--scheduled): this plan would hide too many photos '
            '(over the mass-hide safety threshold) — no photos were hidden.')


# --- level contract ----------------------------------------------------------

def test_should_send_contract():
    from pushframe.report import should_send
    assert should_send('DEBUG', 0, OK_OUT) is True     # every run, traces
    assert should_send('INFO', 0, OK_OUT) is True      # every run, summary
    assert should_send('ERROR', 0, OK_OUT) is False    # healthy run: silent
    assert should_send('ERROR', 1, FAIL_OUT) is True   # failure
    assert should_send('ERROR', 0, TRIP_OUT) is True   # potential problem


# --- one test per ERROR-level marker (markers mirror gsync.py's prints) ------

def test_error_marker_sync_failed_prefix():
    """'google-sync failed: …' — every named failure path in gsync prints
    this prefix; broad net in case a future path forgets to propagate the
    non-zero exit."""
    from pushframe.report import should_send
    assert should_send('ERROR', 1, 'google-sync failed: reading frame '
                                      'assets failed: 401') is True
    assert should_send('ERROR', 0, 'Login failed: bad credentials') is True


def test_error_marker_uploads_failed():
    """'{n} upload(s) FAILED — no manifest entry written; they will retry
    next run' — the MPO-era partial failure: the run exits 0 (retry next
    run) but photos are missing from the frame."""
    from pushframe.report import should_send
    out = '2 upload(s) FAILED — no manifest entry written; they will retry next run'
    assert should_send('ERROR', 0, out) is True


def test_error_marker_all_report_pair_failed():
    """'  [FAILED] pair-name' — the --all per-pair report line."""
    from pushframe.report import should_send
    out = '--- --all report ---\n  [ok] a-first\n  [FAILED] b-second'
    assert should_send('ERROR', 0, out) is True


def test_error_marker_downloads_failed():
    """'{n} download(s) failed and were NOT synced (they will retry next
    run)' — SAFE-04's fail-closed download path."""
    from pushframe.report import should_send
    out = '1 download(s) failed and were NOT synced (they will retry next run)'
    assert should_send('ERROR', 0, out) is True


def test_error_marker_scheduled_mass_hide_skip():
    """'SKIPPED (--scheduled): this plan would hide too many photos …' —
    TMR-03's exit-0-by-design skip: the unit must not fail over a safety
    decision, but the operator must hear about it."""
    from pushframe.report import should_send
    assert should_send('ERROR', 0, TRIP_OUT) is True


def test_error_marker_anti_abuse_stop():
    """'google-sync stopped: …' — the anti-abuse trip body signature."""
    from pushframe.report import should_send
    out = ('google-sync stopped: Client error \'401 Unauthorized\'\n'
           '3 item(s) confirmed written and ARE on the frame; …')
    assert should_send('ERROR', 0, out) is True


def test_error_marker_over_threshold_plan_applied():
    """'⚠ {n} of {m} photos … over the 20% mass-hide safety threshold.' —
    printed when an over-threshold plan IS applied (interactive y or
    --yes): deliberate, but worth hearing about."""
    from pushframe.report import should_send
    out = ('⚠ 9 of 10 photos on "Living Room" (id: 00000000) would be '
           'hidden — over the 20% mass-hide safety threshold.')
    assert should_send('ERROR', 0, out) is True


def test_error_marker_rate_limit_abort_but_not_a_decline():
    """'Aborted: {error}' (with colon) is the RateLimitError abort — a
    problem. 'Aborted.' PLAIN (no colon) is the operator's deliberate 'n'
    at a confirmation prompt — NOT a problem: no email."""
    from pushframe.report import should_send
    assert should_send('ERROR', 0,
                       'Aborted: Client error \'429 Too Many Requests\'') is True
    assert should_send('ERROR', 0, 'Plan: 3 to upload\nAborted.') is False


def test_normalize_level():
    from pushframe.report import normalize_level
    assert normalize_level('debug') == 'DEBUG'
    assert normalize_level(' Error ') == 'ERROR'
    assert normalize_level('verbose') is None
    assert normalize_level(None) is None


def test_subjects_name_the_outcome():
    from pushframe.report import build_subject
    assert 'FAILED' in build_subject('nightly', 'INFO', 1, FAIL_OUT)
    assert 'ATTENTION' in build_subject('nightly', 'ERROR', 0, TRIP_OUT)
    assert '111 uploaded, 2 hidden' in build_subject('nightly', 'INFO', 0,
                                                     HIDE_OUT)
    assert 'nothing to do' in build_subject('nightly', 'INFO', 0, OK_OUT)
    assert build_subject('nightly', 'INFO', 0, HIDE_OUT).startswith(
        '[pushframe] nightly:')


def test_debug_body_carries_log_tail(cfg_path, tmp_path, monkeypatch):
    from pushframe.report import build_body, tail_log
    monkeypatch.chdir(tmp_path)
    assert tail_log() is None                      # no logs dir
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs' / 'file_2026-10-02_01.log').write_text(
        'line1\nline2\nline3\n')
    assert 'line3' in tail_log()
    body = build_body('DEBUG', command='google-sync --pair p', rc=0,
                      started_ts=0.0, duration_s=63.0, output=OK_OUT,
                      log_tail=tail_log())
    assert 'log tail' in body and 'line3' in body
    assert '1:03' in body                          # duration formatting
    assert 'command' in body.lower()


# --- gating + delivery (never fails the run) ---------------------------------

def test_deliver_error_level_skips_healthy_run(cfg_path, monkeypatch):
    _clean_env(monkeypatch)
    from pushframe.report import deliver
    assert deliver('ERROR', command='x', rc=0, output=OK_OUT,
                   duration_s=1.0) is None         # gated, nothing sent


def test_deliver_unconfigured_names_the_remedy(cfg_path, monkeypatch):
    _clean_env(monkeypatch)
    from pushframe.report import deliver
    err = deliver('INFO', command='x', rc=0, output=OK_OUT, duration_s=1.0)
    assert err and 'not configured' in err and '--smtp-host' in err


def test_deliver_send_failure_returns_reason_never_raises(cfg_path, monkeypatch):
    _clean_env(monkeypatch)
    from pushframe.report import deliver
    from pushframe import config_store
    config_store.update(report={'to': 'me@example.com',
                                'smtp_host': 'smtp.example.com'})
    calls = []
    err = deliver('INFO', command='x', rc=0, output=OK_OUT, duration_s=1.0,
                  smtp_factory=_factory(calls, fail_send=True))
    assert err and 'email send failed' in err


def test_deliver_end_to_end_ok_run(cfg_path, monkeypatch):
    _clean_env(monkeypatch)
    from pushframe.report import deliver
    from pushframe import config_store
    config_store.update(report={'to': 'me@example.com',
                                'smtp_host': 'smtp.example.com',
                                'smtp_port': '587',
                                'smtp_user': 'me@example.com',
                                'smtp_password': 'pw'})
    calls = []
    err = deliver('INFO', command='google-sync --pair cadre-venus', rc=0,
                  output=HIDE_OUT, duration_s=109.0, tag='nightly',
                  started_ts=1_700_000_000.0, smtp_factory=_factory(calls))
    assert err is None
    assert ('connect', 'smtp.example.com', 587) == calls[0]
    send = [c for c in calls if c[0] == 'send'][0]
    assert send[1] == 'me@example.com' and send[2] == 'me@example.com'
    assert '[pushframe] nightly:' in send[3]
    assert '111 uploaded, 2 hidden' in send[3]
    body = send[4]
    assert 'google-sync --pair cadre-venus' in body
    assert 'Result:   OK (exit 0)' in body
    assert 'log tail' not in body                   # INFO: no traces


def test_deliver_env_overrides_config(cfg_path, monkeypatch):
    _clean_env(monkeypatch)
    from pushframe.report import deliver
    from pushframe import config_store
    config_store.update(report={'to': 'stored@example.com',
                                'smtp_host': 'stored.example.com'})
    monkeypatch.setenv('PUSHFRAME_REPORT_TO', 'env@example.com')
    monkeypatch.setenv('PUSHFRAME_SMTP_HOST', 'env.example.com')
    calls = []
    deliver('INFO', command='x', rc=0, output=OK_OUT, duration_s=0.0,
            smtp_factory=_factory(calls))
    assert ('connect', 'env.example.com', 587) == calls[0]
    assert calls[-1][2] == 'env@example.com'        # To header


# --- schedule add --report ----------------------------------------------------

def _patch_systemd(monkeypatch):
    from pushframe import schedule as sch
    monkeypatch.setattr(sch, 'systemd_user_session_ok', lambda: True)
    monkeypatch.setattr(sch, 'run_systemctl', lambda *a: ('', ''))


def _pair(name="cadre-venus", album="Cadre", frame="Cadre de Fabrice"):
    from pushframe import pairs as pairs_mod
    pairs_mod.pair_add(name, album=album, frame=frame)


def test_schedule_add_rejects_unknown_level(unit_dir, cfg_path, monkeypatch,
                                            capsys):
    from pushframe import schedule as sch
    _patch_systemd(monkeypatch)
    assert sch.schedule_add('n', pair='ghost', every='1d',
                            report='verbose') == 1
    assert 'DEBUG, INFO or ERROR' in capsys.readouterr().out


def test_schedule_add_report_renders_into_exec_start(unit_dir, cfg_path,
                                                     monkeypatch):
    from pushframe import schedule as sch
    _patch_systemd(monkeypatch)
    _pair()
    assert sch.schedule_add('nightly', pair='cadre-venus', every='1d',
                            report='ERROR') == 0
    svc = (unit_dir / 'pushframe-nightly.service').read_text()
    assert '--report ERROR' in svc
    assert '--report-tag "nightly"' in svc
    assert '--report-to' not in svc                 # no override given


def test_schedule_add_report_without_transport_warns_but_installs(
        unit_dir, cfg_path, monkeypatch, capsys):
    from pushframe import schedule as sch
    _patch_systemd(monkeypatch)
    _pair()
    rc = sch.schedule_add('nightly', pair='cadre-venus', every='1d',
                          report='INFO')
    out = capsys.readouterr().out
    assert rc == 0                                  # the job still installs
    assert 'no email transport is configured' in out
    assert 'schedule report --to' in out


def test_schedule_add_report_with_transport_no_warning(unit_dir, cfg_path,
                                                       monkeypatch, capsys):
    from pushframe import schedule as sch
    _patch_systemd(monkeypatch)
    _pair()
    from pushframe import config_store
    config_store.update(report={'to': 'me@example.com',
                                'smtp_host': 'smtp.example.com'})
    sch.schedule_add('nightly', pair='cadre-venus', every='1d', report='DEBUG')
    assert 'no email transport is configured' not in capsys.readouterr().out


def test_schedule_add_report_sync_dir_is_refused(unit_dir, cfg_path,
                                                 monkeypatch, capsys):
    from pushframe import schedule as sch
    _patch_systemd(monkeypatch)
    assert sch.schedule_add('n', sync_dir='/srv/photos', frame='Salon',
                            every='1d', report='INFO') == 1
    assert 'google-sync' in capsys.readouterr().out


# --- `schedule report` configurator ------------------------------------------

def test_report_configure_show_when_empty(cfg_path, monkeypatch, capsys):
    _clean_env(monkeypatch)
    from pushframe.report import configure
    assert configure([]) == 0
    out = capsys.readouterr().out
    assert 'NOT configured' in out
    assert '--to ADDR --smtp-host HOST' in out


def test_report_configure_set_and_store(cfg_path, monkeypatch, capsys):
    _clean_env(monkeypatch)
    from pushframe.report import configure
    assert configure(['--to', 'me@example.com',
                      '--smtp-host', 'smtp.example.com',
                      '--smtp-user', 'me@example.com'],
                     stdin_isatty=False) == 0
    data = json.loads(cfg_path.read_text())
    assert data['report']['to'] == 'me@example.com'
    assert data['report']['smtp_host'] == 'smtp.example.com'
    assert 'smtp_password' not in data['report']    # non-tty: no prompt, no store
    out = capsys.readouterr().out
    assert 'configured' in out
    assert 'report --test' in out                   # names the next step


def test_report_configure_disable_and_exclusive_flags(cfg_path, capsys):
    from pushframe.report import configure
    from pushframe import config_store
    config_store.update(report={'to': 'x@example.com',
                                'smtp_host': 'smtp.example.com'})
    assert configure(['--disable']) == 0
    assert not json.loads(cfg_path.read_text()).get('report')
    assert configure(['--disable']) == 0            # idempotent
    assert configure(['--show', '--test']) == 2     # exclusive
    assert 'exclusive' in capsys.readouterr().out


def test_report_configure_test_sends_trial(cfg_path, monkeypatch, capsys):
    _clean_env(monkeypatch)
    from pushframe.report import configure
    from pushframe import config_store
    config_store.update(report={'to': 'me@example.com',
                                'smtp_host': 'smtp.example.com'})
    calls = []
    assert configure(['--test'], smtp_factory=_factory(calls)) == 0
    out = capsys.readouterr().out
    assert 'test email sent to me@example.com' in out
    assert '[pushframe] test:' in calls[-1][3]


def test_report_configure_test_without_config_fails_named(cfg_path, monkeypatch,
                                                          capsys):
    _clean_env(monkeypatch)
    from pushframe.report import configure
    assert configure(['--test']) == 1
    assert 'cannot test' in capsys.readouterr().out


def test_report_configure_unknown_argument_prints_usage(cfg_path, capsys):
    from pushframe.report import configure
    assert configure(['--smtp-hoast', 'x']) == 2
    assert 'usage: pushframe schedule report' in capsys.readouterr().out


def test_report_configure_help_prints_the_map(cfg_path, capsys):
    """`schedule report --help` explains the whole configurator without
    touching anything (it used to be just the unknown-argument usage)."""
    from pushframe.report import configure
    assert configure(['--help']) == 0
    out = capsys.readouterr().out
    assert 'usage: pushframe schedule report' in out
    assert 'DEBUG' in out and 'INFO' in out and 'ERROR' in out
    assert '--test' in out and '--disable' in out
    # `help` (the word) takes the same path
    assert configure(['help']) == 0


# --- CLI plumbing ------------------------------------------------------------

def test_gsync_report_flag_sends_and_keeps_exit_code(cfg_path, monkeypatch,
                                                     capsys):
    """--report on a failing google-sync emails it and preserves the sync's
    exit code; --report-to overrides the recipient."""
    _clean_env(monkeypatch)
    from pushframe import config_store, report as report_mod
    config_store.update(report={'to': 'me@example.com',
                                'smtp_host': 'smtp.example.com'})
    sent = []

    def fake_send(cfg, subject, body, *, smtp_factory=None):
        sent.append((cfg['to'], subject, body))

    monkeypatch.setattr(report_mod, 'send', fake_send)

    def fake_run(album_target, frame_arg, **kw):
        print('google-sync failed: reading frame assets failed: 401')
        return 1

    monkeypatch.setattr('pushframe.gsync.run_google_sync', fake_run)
    from pushframe.cli import main
    rc = main(['google-sync', 'Cadre', '--frame', 'Fabrice',
               '--report', 'ERROR', '--report-to', 'boss@example.com'])
    assert rc == 1                                  # the SYNC's code, not mail
    assert len(sent) == 1
    assert sent[0][0] == 'boss@example.com'
    assert 'FAILED' in sent[0][1]
    assert 'command' in sent[0][2].lower()


def test_schedule_subcommand_flags_reach_the_verb(cfg_path, monkeypatch,
                                                  capsys):
    """The schedule passthrough strips its tail like config's does:
    `schedule report --to … --smtp-host …` must reach the configurator
    (its --flags used to die in argparse as unrecognized top-level args)."""
    _clean_env(monkeypatch)
    from pushframe.cli import main
    assert main(['schedule', 'report', '--to', 'me@example.com',
                 '--smtp-host', 'smtp.example.com']) == 0
    out = capsys.readouterr().out
    assert 'configured' in out
    assert main(['schedule', 'report']) == 0
    assert 'smtp.example.com' in capsys.readouterr().out


# --- config set/get/show speak the report keys -------------------------------

def test_config_set_report_key_stores_in_report_block(cfg_path, capsys):
    from pushframe.cli import run_config
    assert run_config(wizard_args=['set', 'report_to', 'me@example.com']) == 0
    data = json.loads(cfg_path.read_text())
    assert data['report']['to'] == 'me@example.com'   # aliased to the block
    out = capsys.readouterr().out
    assert 'report --test' in out                     # names the next step


def test_config_set_help_explains_every_key_group(cfg_path, capsys):
    """`config set --help` is the per-key reference the doc mirrors: every
    accepted key appears, grouped by purpose, with the redaction and
    precedence contracts named."""
    from pushframe.cli import run_config
    assert run_config(wizard_args=['set', '--help']) == 0
    out = capsys.readouterr().out
    assert 'usage: pushframe config set KEY VALUE' in out
    for key in ('default_frame', 'debug', 'report_to', 'smtp_host',
                'smtp_port', 'smtp_user', 'smtp_password', 'report_from',
                'AURA_COUNTRY', 'AURA_GEO_FAIL_OPEN',
                'AURA_WRITE_BUDGET_CAPACITY', 'AURA_WRITE_BUDGET_REFILL_PER_MIN',
                'AURA_WRITE_BUDGET_WAIT', 'AURA_WRITE_BUDGET_MAX_WAIT',
                'DEVICE_IDENTIFIER', 'USER_AGENT', 'LOCALE',
                'AURA_APP_IDENTIFIER', 'AURA_API_BASE_URL',
                'AURA_STATE_DIR', 'AWS_S3_BUCKET'):
        assert key in out, f'{key} missing from config set --help'
    assert '587' in out and '465' in out              # port semantics
    assert 'uuidgen' in out                           # the DEVICE_IDENTIFIER remedy
    assert '***' in out                               # password redaction contract
    assert 'docs/CLI.md' in out


def test_config_set_usage_and_unknown_key_point_to_the_help(cfg_path, capsys):
    from pushframe.cli import run_config
    assert run_config(wizard_args=['set']) == 1
    assert 'config set --help' in capsys.readouterr().out
    rc = run_config(wizard_args=['set', 'NOT_A_KEY', 'x'])
    assert rc == 1
    assert 'config set --help' in capsys.readouterr().out


def test_config_set_smtp_password_is_redacted_in_output(cfg_path, capsys):
    from pushframe.cli import run_config
    assert run_config(wizard_args=['set', 'smtp_password', 's3cret']) == 0  # noqa: S105
    out = capsys.readouterr().out
    assert 's3cret' not in out                        # noqa: S105
    assert "'***'" in out
    assert json.loads(cfg_path.read_text())['report']['smtp_password'] == 's3cret'  # noqa: S105


def test_config_get_report_key_and_unknown_lists_them(cfg_path, monkeypatch,
                                                      capsys):
    _clean_env(monkeypatch)
    from pushframe.cli import run_config
    run_config(wizard_args=['set', 'smtp_host', 'smtp.example.com'])
    assert run_config(wizard_args=['get', 'smtp_host']) == 0
    assert 'smtp.example.com' in capsys.readouterr().out
    # an unknown key's remedy lists the report keys too
    rc = run_config(wizard_args=['get', 'NOT_A_KEY'])
    assert rc == 1
    assert 'report_to' in capsys.readouterr().out


def test_config_get_report_key_env_override(cfg_path, monkeypatch, capsys):
    _clean_env(monkeypatch)
    monkeypatch.setenv('PUSHFRAME_REPORT_TO', 'env@example.com')
    from pushframe.cli import run_config
    run_config(wizard_args=['set', 'report_to', 'file@example.com'])
    assert run_config(wizard_args=['get', 'report_to']) == 0
    out = capsys.readouterr().out
    assert 'env@example.com' in out and '(source: env)' in out


def test_config_show_reports_the_transport_block(cfg_path, monkeypatch, capsys):
    """`config show` must surface the report transport — the operator's only
    discovery path for "how do I configure SMTP?" was reading docs."""
    _clean_env(monkeypatch)
    from pushframe.cli import run_config
    # configured: shows the values, redacts the password, names next steps
    run_config(wizard_args=['set', 'report_to', 'me@example.com'])
    run_config(wizard_args=['set', 'smtp_host', 'smtp.example.com'])
    run_config(wizard_args=['set', 'smtp_password', 's3cret'])  # noqa: S105
    assert run_config(wizard_args=['show']) == 0
    out = capsys.readouterr().out
    assert "report_to = 'me@example.com'  (file)" in out
    assert "smtp_host = 'smtp.example.com'  (file)" in out
    assert "smtp_password = '***'  (file)" in out
    assert 's3cret' not in out                        # noqa: S105
    assert 'schedule report --test' in out


def test_config_show_is_an_exhaustive_inventory(cfg_path, monkeypatch, capsys):
    """An inventory is only useful if it is exhaustive: every key pushframe
    reads is LISTED — with `(not set)` status when absent — otherwise there
    is no way to discover a setting exists (2026-10-02 report)."""
    _clean_env(monkeypatch)
    from pushframe.cli import run_config
    assert run_config(wizard_args=['show']) == 0
    out = capsys.readouterr().out
    # wizard keys: always listed, absent = (not set)
    assert 'email = (not set)' in out
    assert 'default_frame = (not set)' in out
    assert 'auth_token = (not set)' in out
    assert 'debug = False  (default)' in out
    # all six report keys listed even when nothing is configured
    assert 'email reports (google-sync --report):' in out
    for key in ('report_to', 'smtp_host', 'smtp_user',
                'smtp_password', 'report_from'):
        assert f'{key} = (not set)' in out
    assert 'smtp_port = (not set — 587 by default; 465 = SSL)' in out
    assert 'not configured' in out and 'schedule report --test' in out
    # pairs: the mapping store is named with its remedy
    assert 'pairs: none' in out and 'config pair add' in out


def test_config_show_lists_pairs_and_report_values(cfg_path, monkeypatch,
                                                   capsys):
    _clean_env(monkeypatch)
    from pushframe.cli import run_config
    from pushframe import pairs as pairs_mod
    pairs_mod.pair_add('cadre-venus', album='Cadre', frame='Cadre de Fabrice')
    run_config(wizard_args=['set', 'report_to', 'me@example.com'])
    run_config(wizard_args=['set', 'smtp_port', '465'])
    assert run_config(wizard_args=['show']) == 0
    out = capsys.readouterr().out
    assert 'pairs: 1 (cadre-venus)' in out and 'config pair list' in out
    assert "smtp_port = '465'  (file)" in out          # explicit value shown
    assert '587 by default' not in out                 # only when unset
    assert '(trial:' in out                            # configured next step


def test_config_show_env_overrides_report_block(cfg_path, monkeypatch, capsys):
    _clean_env(monkeypatch)
    monkeypatch.setenv('PUSHFRAME_SMTP_HOST', 'env.example.com')
    from pushframe.cli import run_config
    run_config(wizard_args=['set', 'report_to', 'me@example.com'])
    run_config(wizard_args=['set', 'smtp_host', 'file.example.com'])
    assert run_config(wizard_args=['show']) == 0
    out = capsys.readouterr().out
    assert "smtp_host = 'env.example.com'  (env)" in out
    assert "report_to = 'me@example.com'  (file)" in out


def test_schedule_bare_help_still_prints_the_full_map(capsys):
    """5.1.13 regression: the tail stripping swallowed bare `schedule
    --help` — it printed one usage line instead of the WHAT GETS SCHEDULED
    / EMAIL REPORTS / EXAMPLES epilog. Bare --help stays argparse's."""
    with pytest.raises(SystemExit) as exc:
        from pushframe.cli import main
        main(['schedule', '--help'])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert 'WHAT GETS SCHEDULED' in out
    assert 'EMAIL REPORTS' in out
    assert 'EXAMPLES' in out
    assert 'schedule add nightly --pair family --every 1d --report INFO' in out


def test_gsync_report_level_error_silent_on_success(cfg_path, monkeypatch,
                                                    capsys):
    _clean_env(monkeypatch)
    from pushframe import config_store
    config_store.update(report={'to': 'me@example.com',
                                'smtp_host': 'smtp.example.com'})
    calls = []

    def fake_run(album_target, frame_arg, **kw):
        print('Nothing to do — the frame already mirrors the album.')
        return 0

    monkeypatch.setattr('pushframe.gsync.run_google_sync', fake_run)
    from pushframe.cli import main
    rc = main(['google-sync', 'Cadre', '--frame', 'Fabrice',
               '--report', 'ERROR'])
    assert rc == 0
    assert [c for c in calls if c[0] == 'send'] == []   # gated
