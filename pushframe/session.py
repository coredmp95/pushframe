"""Phase 24 (SEC-01/02): establish_session — the ONE session path.

Every API command funnels here (24-RESEARCH §1). Precedence:

  1. env password present   → aura.login()                     (override, discouraged)
  2. config auth_token      → resume_session(email, token, uid) — NO login call
  3. TTY                    → ONE password prompt, login, PERSIST token only
  4. nothing                → NoCredentialsError with the remedy

Expiry (D-01, locked at discuss): a 401 while resuming the stored token
means the token expired/revoked. In a TTY: ONE password prompt (reusing
the wizard seam), the new token is persisted (SEC-01: never the password),
and the command continues. In a non-TTY (scheduled jobs): the prompt must
never hang — SessionExpiredError, named, with the remedy.

SEC-01 hard rule: the password is used in-memory for the login call and
discarded at process exit — it is NEVER written to config.json (asserted
by file-content tests, not just behavior).
"""
import getpass

from pushframe import config_store
from pushframe.utils import settings


class SessionError(Exception):
    """Base for named session failures — each carries the remedy."""


class NoCredentialsError(SessionError):
    pass


class SessionExpiredError(SessionError):
    pass


def _is_auth_failure(exc: Exception) -> bool:
    """True when an exception is the 401 signature (expired/revoked token).
    Corrupt config or network errors are NOT auth failures — they must not
    be swallowed into a password prompt."""
    return '401' in str(exc) and 'unauthorized' in str(exc).lower()


def _store_token(email: str, auth_token: str, user_id: str | None) -> None:
    """Persist the session facts — token only, never the password (SEC-01)."""
    data = {'email': email, 'auth_token': auth_token}
    if user_id:
        data['user_id'] = user_id
    existing = config_store.load()
    shadowed = settings.shadowed_keys(existing.get('settings', {}))
    config_store.update(**data)


def _wizard_login(email: str, password: str) -> dict:
    """Module-level seam (tests patch THIS, never the network). Lazy import:
    cli.py imports session at startup, so session must not import cli at
    module load."""
    from pushframe.cli import _wizard_login as _impl
    return _impl(email, password)


def _prompt_login(aura, stdin_isatty: bool):
    """The ONE interactive login (wizard seam), then persist the token.

    Returns the Aura with fresh auth headers attached. Raises
    SessionExpiredError in a non-TTY instead of prompting (scheduled jobs
    must fail named, not hang)."""
    if not stdin_isatty:
        raise SessionExpiredError(
            'stored session expired and stdin is not a terminal — re-run '
            '`pushframe config` once interactively (or set PUSHFRAME_EMAIL/'
            'PUSHFRAME_PASSWORD for this run)')
    stored_email = config_store.load().get('email')
    if stored_email:
        print(f"configuring pushframe (current email: {stored_email} — Enter keeps it)")
    email = input('Aura email: ').strip()
    if not email and stored_email:
        # Same contract as the wizard's prompt (fixed in 5.1.2): the banner
        # promised "Enter keeps it" — an empty answer keeps the stored email
        # instead of feeding an empty email to the login.
        email = stored_email
    if not email:
        print('no email given — aborting, nothing stored.')
        return None
    password = getpass.getpass('Aura password (input hidden): ')
    result = _wizard_login(email, password)
    _store_token(result['email'], result['auth_token'], result.get('user_id'))
    aura.login(email=email, password=password)
    return aura


def _prompt_login_or_fail(aura, stdin_isatty):
    """_prompt_login may abort (empty email on a never-configured host);
    establish_session must surface that as the named no-credentials error
    instead of returning None where an Aura was expected."""
    result = _prompt_login(aura, stdin_isatty)
    if result is None:
        raise NoCredentialsError(
            'login aborted (no email given) — no session stored; '
            're-run the command to retry')
    return result


def establish_session(aura=None, *, stdin_isatty: bool | None = None,
                      do_prompt: bool = True):
    """Return an authenticated Aura. See module docstring for precedence.

    `aura` is injectable for tests (an object exposing login/resume_session).
    `stdin_isatty=None` consults sys.stdin (tests pass True/False explicitly).
    """
    import os
    import sys

    if aura is None:
        from pushframe.aura import Aura
        aura = Aura()
    if stdin_isatty is None:
        stdin_isatty = sys.stdin.isatty()

    # 1. env password — the override (discouraged for humans, the CI path)
    env_email = os.getenv('PUSHFRAME_EMAIL') or os.getenv('AURA_EMAIL')
    env_password = os.getenv('PUSHFRAME_PASSWORD') or os.getenv('AURA_PASSWORD')
    if env_email and env_password:
        aura.login(email=env_email, password=env_password)
        return aura

    # 2. stored session — resume, never a login call
    stored = config_store.load()
    if stored.get('email') and stored.get('auth_token'):
        try:
            return aura.resume_session(email=stored['email'],
                                       auth_token=stored['auth_token'],
                                       user_id=stored.get('user_id'))
        except Exception as e:
            if not _is_auth_failure(e):
                raise  # corrupt config, network — never masked as expiry
            # D-01: expired token. TTY → one prompt and continue;
            # non-TTY → named error (a scheduled run cannot answer).
            if not (do_prompt and stdin_isatty):
                raise SessionExpiredError(
                    f'stored session expired ({e}) and stdin is not a '
                    f'terminal — re-run `pushframe config` once '
                    f'interactively') from e
            print('stored session expired — one re-login to refresh it:')
            return _prompt_login_or_fail(aura, stdin_isatty)

    # 3. interactive first-time setup (same ONE prompt)
    if do_prompt and stdin_isatty:
        return _prompt_login_or_fail(aura, stdin_isatty)

    # 4. nothing available — named, with the remedy
    raise NoCredentialsError(
        'no credentials: set PUSHFRAME_EMAIL/PUSHFRAME_PASSWORD (override) '
        'or run `pushframe config` once to store a session')


def _env_password_present() -> bool:
    """The override check shared with sync.execute_plan's relogin seam."""
    import os
    return bool((os.getenv('PUSHFRAME_EMAIL') or os.getenv('AURA_EMAIL'))
                and (os.getenv('PUSHFRAME_PASSWORD') or os.getenv('AURA_PASSWORD')))


def _tty_available() -> bool:
    import sys
    return sys.stdin.isatty()
