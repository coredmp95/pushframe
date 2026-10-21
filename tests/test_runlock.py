"""Per-account run lock (quick-261021-c1k): module contract + real two-process
contention. Every test runs against a tmp lock dir (conftest autouse), never
the real ~/.local/state/pushframe."""
import fcntl
import hashlib
import json
import os
import select
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from pushframe import runlock

REPO_ROOT = Path(__file__).resolve().parent.parent
EMAIL = 'a@b.c'


# --- helpers -----------------------------------------------------------------

def _hold(path: Path, holder: dict | bytes | None):
    """Raw-fd holder: flock(LOCK_EX) plus optional holder-info bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    if isinstance(holder, dict):
        os.pwrite(fd, json.dumps(holder).encode(), 0)
    elif isinstance(holder, bytes):
        os.pwrite(fd, holder, 0)
    return fd


SALON = {'pid': 4242, 'job': 'SALON-CADRE', 'command': 'google-sync',
         'started_at': '2026-10-21T00:13:00'}


class FakeClock:
    def __init__(self, release_at=None, on_release=None):
        self.t = 0.0
        self.slept = 0.0
        self.sleeps = 0
        self.release_at = release_at
        self.on_release = on_release

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps += 1
        self.slept += seconds
        self.t += seconds
        if self.release_at is not None and self.t >= self.release_at:
            self.on_release()
            self.release_at = None


def _now():
    return datetime(2026, 10, 21, 10, 0, 0)


# --- keys and paths (U-01) ---------------------------------------------------

def test_account_key_is_the_sha1_12_filename_key():
    # Pinned: persisted budget-<key>.json files on deployed hosts must keep
    # being found -- the digest, truncation and (absent) normalization are a
    # compatibility contract.
    assert runlock.account_key(EMAIL) == 'e2d6a40c8b4d'
    assert runlock.account_key(EMAIL) == hashlib.sha1(EMAIL.encode()).hexdigest()[:12]
    assert runlock.account_key('coredmp95@gmail.com') == 'eeda3bb09fc5'
    # no case folding / stripping: a different spelling is a different key
    assert runlock.account_key('A@b.c') != runlock.account_key(EMAIL)


def test_budget_and_lock_share_the_account_key(tmp_path, monkeypatch):
    from pushframe import cli
    monkeypatch.setattr(cli, 'AURA_STATE_DIR', tmp_path / 'state')
    budget = cli._build_write_budget(EMAIL, False)
    key = runlock.account_key(EMAIL)
    assert budget.path.name == f'budget-{key}.json'
    assert runlock.lock_path_for(EMAIL).name == f'account-{key}.lock'


def test_lock_dir_is_resolved_at_call_time(monkeypatch, tmp_path):
    monkeypatch.setattr(runlock, 'LOCK_DIR_OVERRIDE', None)
    monkeypatch.setenv('HOME', str(tmp_path))
    assert runlock.lock_dir() == tmp_path / '.local/state/pushframe/locks'
    other = tmp_path / 'elsewhere'
    monkeypatch.setattr(runlock, 'LOCK_DIR_OVERRIDE', other)
    assert runlock.lock_dir() == other
    assert runlock.lock_path_for(EMAIL).parent == other


# --- uncontended -------------------------------------------------------------

def test_uncontended_acquire_is_silent_and_writes_holder_info(tmp_path, capsys):
    d = tmp_path / 'locks'
    lock = runlock.acquire_account_lock(EMAIL, job='nightly', command='sync',
                                        max_wait=60, lock_dir=d)
    try:
        out = capsys.readouterr()
        assert out.out == '' and out.err == ''
        path = runlock.lock_path_for(EMAIL, lock_dir=d)
        raw = path.read_bytes()
        info = json.loads(raw)
        assert info['pid'] == os.getpid()
        assert info['job'] == 'nightly' and info['command'] == 'sync'
        assert 'started_at' in info
        assert EMAIL.encode() not in raw           # T-c1k-01
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(d.stat().st_mode) == 0o700
    finally:
        lock.release()


def test_release_truncates_unlocks_and_is_idempotent(tmp_path):
    d = tmp_path / 'locks'
    lock = runlock.acquire_account_lock(EMAIL, job='j', command='sync',
                                        max_wait=0, lock_dir=d)
    path = runlock.lock_path_for(EMAIL, lock_dir=d)
    lock.release()
    lock.release()                                  # idempotent
    assert path.exists() and path.read_bytes() == b''   # never unlinked
    again = runlock.acquire_account_lock(EMAIL, job='j', command='sync',
                                         max_wait=0, lock_dir=d)
    again.release()


def test_context_manager_releases_on_exception(tmp_path):
    d = tmp_path / 'locks'
    with pytest.raises(RuntimeError):
        with runlock.account_run_lock(EMAIL, job='j', command='sync',
                                      max_wait=0, lock_dir=d) as lock:
            assert lock is not None
            raise RuntimeError('boom')
    runlock.acquire_account_lock(EMAIL, job='j', command='sync', max_wait=0,
                                 lock_dir=d).release()


def test_context_manager_without_identity_skips_with_stderr_note(capsys):
    with runlock.account_run_lock(None, job=None, command='sync',
                                  max_wait=0) as lock:
        assert lock is None
    err = capsys.readouterr()
    assert 'account lock: no account identity' in err.err
    assert err.out == ''


# --- contention with a fake clock -------------------------------------------

def test_contended_wait_logs_holder_every_five_minutes_then_acquires(tmp_path):
    d = tmp_path / 'locks'
    path = runlock.lock_path_for(EMAIL, lock_dir=d)
    fd = _hold(path, SALON)
    clock = FakeClock(release_at=900, on_release=lambda: os.close(fd))
    lines = []
    lock = runlock.acquire_account_lock(
        EMAIL, job='nightly', command='sync', max_wait=43200,
        poll_interval=5, log_every=300, sleep=clock.sleep,
        monotonic=clock.monotonic, now=_now, out=lines.append, lock_dir=d)
    try:
        waiting = [ln for ln in lines if ln.startswith('waiting')]
        assert len(waiting) == 3
        for ln in waiting:
            assert ('waiting for account lock held by job SALON-CADRE '
                    '(pid 4242, google-sync)') in ln
            assert 'since 00:13' in ln
            assert 'PUSHFRAME_ACCOUNT_LOCK_MAX_WAIT' in ln
        assert 'waited 0m of max 12h00m' in waiting[0]
        assert 'waited 5m of max 12h00m' in waiting[1]
        assert 'waited 10m of max 12h00m' in waiting[2]
        assert lines[-1].startswith('account lock acquired after 15m')
        assert len(lines) == 4
        # the new holder wrote its own info
        assert json.loads(path.read_bytes())['job'] == 'nightly'
    finally:
        lock.release()


def test_cap_raises_named_timeout_with_remedy(tmp_path):
    d = tmp_path / 'locks'
    fd = _hold(runlock.lock_path_for(EMAIL, lock_dir=d), SALON)
    clock = FakeClock()
    try:
        with pytest.raises(runlock.AccountLockTimeout) as ei:
            runlock.acquire_account_lock(
                EMAIL, job='nightly', command='sync', max_wait=3600,
                poll_interval=5, log_every=300, sleep=clock.sleep,
                monotonic=clock.monotonic, now=_now, out=lambda m: None,
                lock_dir=d)
    finally:
        os.close(fd)
    msg = str(ei.value)
    assert 'account lock still held by job SALON-CADRE (pid 4242' in msg
    assert 'PUSHFRAME_ACCOUNT_LOCK_MAX_WAIT' in msg
    assert 'systemctl --user stop pushframe-SALON-CADRE.service' in msg
    assert 'this run changed nothing' in msg
    assert 'never delete the lock file' in msg
    assert clock.slept <= 3600
    assert ei.value.holder['pid'] == 4242
    assert ei.value.waited_seconds >= 3600
    assert isinstance(ei.value, runlock.AccountLockError)


def test_zero_cap_waits_never(tmp_path):
    d = tmp_path / 'locks'
    fd = _hold(runlock.lock_path_for(EMAIL, lock_dir=d), SALON)
    clock = FakeClock()
    lines = []
    try:
        with pytest.raises(runlock.AccountLockTimeout):
            runlock.acquire_account_lock(
                EMAIL, job='n', command='sync', max_wait=0, sleep=clock.sleep,
                monotonic=clock.monotonic, now=_now, out=lines.append,
                lock_dir=d)
    finally:
        os.close(fd)
    assert clock.sleeps == 0
    assert len([ln for ln in lines if ln.startswith('waiting')]) == 1


@pytest.mark.parametrize('payload', [b'', b'{not json', b'[1, 2]', b'{"pid": "x"}'])
def test_unreadable_holder_info_degrades_without_crash(tmp_path, payload):
    d = tmp_path / 'locks'
    fd = _hold(runlock.lock_path_for(EMAIL, lock_dir=d), payload)
    lines = []
    try:
        with pytest.raises(runlock.AccountLockTimeout) as ei:
            runlock.acquire_account_lock(
                EMAIL, job='n', command='sync', max_wait=0,
                sleep=lambda s: None, monotonic=lambda: 0.0, now=_now,
                out=lines.append, lock_dir=d)
    finally:
        os.close(fd)
    assert 'held by another pushframe run (holder details unavailable)' in lines[0]
    assert ' since ' not in lines[0].split('— waited')[0]
    assert 'find it with: pgrep -af pushframe' in str(ei.value)


def test_holder_strings_are_sanitized_before_display(tmp_path):
    d = tmp_path / 'locks'
    evil = {'pid': 7, 'job': 'x\nINJECTED\x1b[31m' + 'y' * 100,
            'command': 'sync;rm', 'started_at': '2026-10-21T00:13:00'}
    fd = _hold(runlock.lock_path_for(EMAIL, lock_dir=d), evil)
    lines = []
    try:
        with pytest.raises(runlock.AccountLockTimeout) as ei:
            runlock.acquire_account_lock(
                EMAIL, job='n', command='sync', max_wait=0,
                sleep=lambda s: None, monotonic=lambda: 0.0, now=_now,
                out=lines.append, lock_dir=d)
    finally:
        os.close(fd)
    shown = lines[0] + str(ei.value)
    assert '\x1b' not in shown and 'INJECTED' in shown   # chars stripped, text kept
    assert '\n' not in lines[0]
    job_shown = lines[0].split('held by job ')[1].split(' (pid')[0]
    assert len(job_shown) <= 64
    assert ';' not in lines[0]


def test_holder_without_job_names_the_command_and_pid(tmp_path):
    d = tmp_path / 'locks'
    fd = _hold(runlock.lock_path_for(EMAIL, lock_dir=d),
               {'pid': 99, 'command': 'push', 'started_at': '2026-10-20T23:00:00'})
    lines = []
    try:
        with pytest.raises(runlock.AccountLockTimeout) as ei:
            runlock.acquire_account_lock(
                EMAIL, job=None, command='sync', max_wait=0,
                sleep=lambda s: None, monotonic=lambda: 0.0, now=_now,
                out=lines.append, lock_dir=d)
    finally:
        os.close(fd)
    assert 'held by a push run (pid 99)' in lines[0]
    assert 'since 2026-10-20 23:00' in lines[0]      # other day -> dated
    assert 'end pid 99' in str(ei.value)


def test_unwritable_lock_dir_is_a_named_error(tmp_path):
    if os.getuid() == 0:
        pytest.skip('root ignores directory permissions')
    ro = tmp_path / 'ro'
    ro.mkdir()
    ro.chmod(0o500)
    try:
        with pytest.raises(runlock.AccountLockError) as ei:
            runlock.acquire_account_lock(EMAIL, job='j', command='sync',
                                         max_wait=0, lock_dir=ro / 'locks')
    finally:
        ro.chmod(0o700)
    assert not isinstance(ei.value, runlock.AccountLockTimeout)
    assert str(ro) in str(ei.value)
    assert 'nothing was changed' in str(ei.value)


def test_reentrant_acquire_is_a_noop_and_outer_release_frees(tmp_path):
    d = tmp_path / 'locks'
    path = runlock.lock_path_for(EMAIL, lock_dir=d)
    outer = runlock.acquire_account_lock(EMAIL, job='j', command='sync',
                                         max_wait=0, lock_dir=d)
    inner = runlock.acquire_account_lock(EMAIL, job='j', command='sync',
                                         max_wait=0, lock_dir=d)
    inner.release()                      # no-op: outer still holds
    probe = os.open(path, os.O_RDWR)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        outer.release()
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)   # now free
    finally:
        os.close(probe)


def test_other_accounts_are_never_blocked(tmp_path):
    d = tmp_path / 'locks'
    fd = _hold(runlock.lock_path_for(EMAIL, lock_dir=d), SALON)
    try:
        other = runlock.acquire_account_lock('other@b.c', job='j',
                                             command='sync', max_wait=0,
                                             lock_dir=d)
        other.release()
    finally:
        os.close(fd)


# --- real two-process contention (U-06) --------------------------------------

CHILD = """
import sys, time
from pathlib import Path
from pushframe import runlock
lock = runlock.acquire_account_lock(
    'a@b.c', job='SALON-CADRE', command='google-sync', max_wait=0,
    lock_dir=Path(sys.argv[1]))
print('LOCKED', flush=True)
time.sleep(60)
"""


def test_two_processes_exclude_each_other_and_sigkill_releases(tmp_path):
    d = tmp_path / 'locks'
    child = subprocess.Popen([sys.executable, '-c', CHILD, str(d)],
                             cwd=REPO_ROOT, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        ready, _, _ = select.select([child.stdout], [], [], 10)
        assert ready, f'child never locked: {child.stderr.read() if child.poll() is not None else "timeout"}'
        assert child.stdout.readline().strip() == 'LOCKED'

        lines = []
        with pytest.raises(runlock.AccountLockTimeout) as ei:
            runlock.acquire_account_lock(
                EMAIL, job='parent', command='sync', max_wait=0.3,
                poll_interval=0.05, out=lines.append, lock_dir=d)
        assert f'pid {child.pid}' in str(ei.value)
        assert 'SALON-CADRE' in str(ei.value)
        assert any(ln.startswith('waiting') for ln in lines)

        # another account is not blocked meanwhile
        runlock.acquire_account_lock('other@b.c', job='j', command='sync',
                                     max_wait=0, lock_dir=d).release()

        child.kill()                     # SIGKILL: no cleanup code runs
        child.wait(timeout=10)
        lock = runlock.acquire_account_lock(EMAIL, job='parent',
                                            command='sync', max_wait=0,
                                            lock_dir=d)
        lock.release()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        child.stdout.close()
        child.stderr.close()


# --- CLI tracer ---------------------------------------------------------------

@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    from pushframe.utils import settings
    monkeypatch.setattr(settings, 'CONFIG_PATH', tmp_path / 'config.json')
    monkeypatch.setenv('PUSHFRAME_EMAIL', EMAIL)
    monkeypatch.delenv('AURA_EMAIL', raising=False)
    monkeypatch.delenv('PUSHFRAME_ACCOUNT_LOCK_MAX_WAIT', raising=False)
    return tmp_path


def _probe_held():
    """True when the account lock is currently held (raw non-blocking probe)."""
    path = runlock.lock_path_for(EMAIL)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def test_sync_apply_runs_under_the_real_lock_and_dry_run_does_not(
        cli_env, monkeypatch, capsys):
    from pushframe import cli
    held = []
    monkeypatch.setattr(cli, 'run_sync',
                        lambda *a, **k: held.append(_probe_held()) or 0)
    d = str(cli_env)
    rc = cli.main(['sync', d, '--frame', 'X', '--apply', '--yes',
                   '--scheduled', '--report-tag', 'nightly'])
    out = capsys.readouterr().out
    assert rc == 0 and held == [True]
    assert out.splitlines()[0].startswith('=== run ')
    assert not _probe_held()             # released after the run

    rc = cli.main(['sync', d, '--frame', 'X'])
    assert rc == 0 and held == [True, False]
