"""Per-account run lock: one Aura write run per account at a time.

Contract (quick-261021-c1k):

* PER ACCOUNT. The lock file is `<lock_dir>/account-<account_key>.lock`, keyed
  by the SAME `account_key` as the persisted WriteBudget state file, so the
  budget's load-consume-save cycle of locked runs is serialized per account.
* KERNEL HELD. `fcntl.flock(LOCK_EX)` on an open file description. The kernel
  drops it the moment the process exits for ANY reason (crash, SIGKILL), so
  there is no stale-lock cleanup anywhere.
* WHOLE RUN. Taken at CLI dispatch around the entire run (session, listing,
  plan, apply), never just the apply phase: a run that waited must plan from
  the state the previous run left, not from a stale listing.
* NEVER UNLINKED. Unlinking a live lock file lets two processes hold two
  different inodes at once. The file stays; only its holder info is truncated.
* NO SECRETS. The file body is holder info only: pid, job tag, verb and start
  time. No email, token, album, link or path.
* POLL WITH A CAP. A bare blocking `flock` cannot honor a safety cap or log
  periodically without threads or signals, so the wait polls
  `flock(LOCK_EX | LOCK_NB)` every `poll_interval` seconds. Same semantics
  (the kernel arbitrates, release on death, waiters not FIFO) at no more than
  a few seconds of extra latency on an hours-long wait, and fully injectable
  (`sleep`, `monotonic`, `now`, `out`) for offline tests.
"""
import contextlib
import fcntl
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from pushframe.utils.io import ensure_private_dir

# Patched by tests (tests/conftest.py points it at a fresh tmp dir per test so
# no test ever touches the real ~/.local/state/pushframe).
LOCK_DIR_OVERRIDE: Path | None = None

CAP_ENV = 'PUSHFRAME_ACCOUNT_LOCK_MAX_WAIT'

_SAFE_RE = re.compile(r'[^A-Za-z0-9._ -]')
_MAX_DISPLAY = 64

# Lock paths (str) held by THIS process: a second acquire of a held path is a
# no-op, because flock conflicts between two open file descriptions of the
# same process and would self-deadlock.
_HELD: set[str] = set()


class AccountLockError(Exception):
    """A named, remedy-carrying failure to take the account lock. Nothing was
    changed when it is raised."""


class AccountLockTimeout(AccountLockError):
    def __init__(self, message: str, *, holder: dict | None = None,
                 waited_seconds: float = 0.0):
        super().__init__(message)
        self.holder = holder
        self.waited_seconds = waited_seconds


class AccountLock:
    """A held account lock. `fd` is None for a re-entrant no-op handle."""

    def __init__(self, path: Path, fd: int | None):
        self.path = path
        self.fd = fd

    def release(self) -> None:
        fd, self.fd = self.fd, None
        if fd is None:
            return
        _HELD.discard(str(self.path))
        try:
            os.ftruncate(fd, 0)
        except OSError:
            pass
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass


# --- paths -------------------------------------------------------------------

def lock_dir() -> Path:
    """The lock directory, resolved at call time (HOME honoured)."""
    if LOCK_DIR_OVERRIDE is not None:
        return Path(LOCK_DIR_OVERRIDE)
    return Path('~/.local/state/pushframe/locks').expanduser()


_default_lock_dir = lock_dir


def account_key(email: str) -> str:
    """THE account filename key shared by the WriteBudget state file and the
    run lock. It is a filename-uniqueness key, NOT a security hash (semgrep
    flags sha1; `usedforsecurity=False` does not change the digest).

    Byte-compatibility contract: sha1 of the raw UTF-8 email, first 12 hex
    chars, no normalization -- persisted `budget-<key>.json` files on
    deployed hosts must keep being found."""
    return hashlib.sha1(email.encode(), usedforsecurity=False).hexdigest()[:12]


def lock_path_for(email: str, *, lock_dir: Path | None = None) -> Path:
    base = Path(lock_dir) if lock_dir is not None else _default_lock_dir()
    return base / f'account-{account_key(email)}.lock'


# --- formatting ---------------------------------------------------------------

def _fmt_dur(seconds: float) -> str:
    minutes = int(max(seconds, 0) // 60)
    if minutes < 60:
        return f'{minutes}m'
    return f'{minutes // 60}h{minutes % 60:02d}m'


def _clean(value) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = _SAFE_RE.sub('', value)[:_MAX_DISPLAY].strip()
    return cleaned or None


def _holder_view(holder: dict | None) -> dict:
    """Display-safe holder fields: pid (int only), job and command
    (sanitized, T-c1k-02), started_at (datetime or None)."""
    holder = holder if isinstance(holder, dict) else {}
    pid = holder.get('pid')
    pid = pid if isinstance(pid, int) and not isinstance(pid, bool) else None
    started = None
    raw = holder.get('started_at')
    if isinstance(raw, str):
        try:
            started = datetime.fromisoformat(raw)
        except ValueError:
            started = None
    return {'pid': pid, 'job': _clean(holder.get('job')),
            'command': _clean(holder.get('command')), 'started': started}


def _who(view: dict) -> str:
    if view['job'] is None and view['pid'] is None and view['command'] is None:
        return 'another pushframe run (holder details unavailable)'
    if view['job']:
        extras = []
        if view['pid'] is not None:
            extras.append(f'pid {view["pid"]}')
        if view['command']:
            extras.append(view['command'])
        return f'job {view["job"]} ({", ".join(extras)})' if extras else f'job {view["job"]}'
    cmd = view['command'] or 'pushframe'
    pid = f' (pid {view["pid"]})' if view['pid'] is not None else ''
    return f'a {cmd} run{pid}'


def _since(view: dict, now: datetime) -> str:
    """' since HH:MM' (dated when not the waiter's current date) or ''."""
    started = view['started']
    if started is None:
        return ''
    local = started.astimezone()
    today = now.astimezone().date()
    stamp = local.strftime('%H:%M') if local.date() == today \
        else local.strftime('%Y-%m-%d %H:%M')
    return f' since {stamp}'


def _stop_hint(view: dict) -> str:
    if view['job']:
        return f'systemctl --user stop pushframe-{view["job"]}.service'
    if view['pid'] is not None:
        return f'end pid {view["pid"]}'
    return 'find it with: pgrep -af pushframe'


def _read_holder(fd: int) -> dict | None:
    try:
        raw = os.pread(fd, 4096, 0)
        data = json.loads(raw.decode('utf-8', 'replace'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --- acquire --------------------------------------------------------------------

def _print_flush(message: str) -> None:
    print(message, flush=True)


def _default_now() -> datetime:
    return datetime.now().astimezone()


def acquire_account_lock(email: str, *, job: str | None, command: str,
                         max_wait: float, poll_interval: float = 5.0,
                         log_every: float = 300.0, sleep=time.sleep,
                         monotonic=time.monotonic, now=None, out=None,
                         lock_dir: Path | None = None) -> AccountLock:
    """Take the account's run lock, waiting (and saying who holds it) up to
    `max_wait` seconds. Raises AccountLockTimeout past the cap and
    AccountLockError when the lock file cannot be opened -- both named, both
    changed nothing."""
    now = now or _default_now
    out = out or _print_flush
    path = lock_path_for(email, lock_dir=lock_dir)
    if str(path) in _HELD:
        return AccountLock(path, None)

    try:
        ensure_private_dir(path.parent)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600)
    except OSError as exc:
        raise AccountLockError(
            f'cannot open the account lock file {path} ({exc.strerror or exc}) '
            f'— fix the permissions of {path.parent}; nothing was changed'
        ) from exc

    try:
        os.fchmod(fd, 0o600)
        start = monotonic()
        next_log = 0.0
        waited = False
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                pass
            except OSError as exc:
                raise AccountLockError(
                    f'cannot lock the account lock file {path} '
                    f'({exc.strerror or exc}); nothing was changed') from exc
            waited = True
            elapsed = monotonic() - start
            holder = _read_holder(fd)
            view = _holder_view(holder)
            if elapsed >= next_log:
                out(f'waiting for account lock held by {_who(view)}'
                    f'{_since(view, now())} — waited {_fmt_dur(elapsed)} of '
                    f'max {_fmt_dur(max_wait)} ({CAP_ENV}); this run starts '
                    'when that one ends')
                next_log += log_every
            if elapsed >= max_wait:
                raise AccountLockTimeout(
                    f'account lock still held by {_who(view)}'
                    f'{_since(view, now())} after waiting '
                    f'{_fmt_dur(elapsed)} — this run changed nothing. If that '
                    f'run is legitimately long, raise {CAP_ENV} (seconds, '
                    f'default 43200 = 12h); if it is stuck, stop it '
                    f'({_stop_hint(view)}) — the lock frees itself the moment '
                    'that process exits; never delete the lock file.',
                    holder=holder, waited_seconds=elapsed)
            sleep(min(poll_interval, max_wait - elapsed))
        if waited:
            out(f'account lock acquired after {_fmt_dur(monotonic() - start)}'
                ' — starting')
        info = json.dumps({'pid': os.getpid(), 'job': job, 'command': command,
                           'started_at': now().isoformat(timespec='seconds')})
        os.ftruncate(fd, 0)
        os.pwrite(fd, info.encode(), 0)
    except BaseException:
        os.close(fd)
        raise
    _HELD.add(str(path))
    return AccountLock(path, fd)


@contextlib.contextmanager
def account_run_lock(email: str | None, **kwargs):
    """Hold the account lock for the `with` body; yields the lock (None when
    the run has no account identity -- then the lock is skipped with a named
    stderr note, mirroring the write-budget "pacing skipped" contract)."""
    if not email:
        print('account lock: no account identity (env or stored session) '
              '— run lock skipped for this run', file=sys.stderr)
        yield None
        return
    lock = acquire_account_lock(email, **kwargs)
    try:
        yield lock
    finally:
        lock.release()
