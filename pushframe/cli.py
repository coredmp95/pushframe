import argparse
import hashlib
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from tqdm import tqdm

from pushframe.aura import Aura
from pushframe.aws.s3client import S3Client
from pushframe.aws.sqsclient import SQSClient
import httpx

from pushframe.client import RateLimitError
from pushframe.google.bootstrap import BootstrapError, PROFILE_ENV_VAR, default_bootstrap
from pushframe.google.client import GoogleSession
from pushframe.google.enumerate import (
    EnumerateError,
    enumerate_album,
    list_shared_albums,
    measure_disk_weight,
)
from pushframe.google.parsers import AlbumSummary
from pushframe.google.redaction import redact_link, redact_tokens
from pushframe.google.vault import CookieVaultError
from pushframe.models.frame import Frame
from pushframe.ratelimit import WriteBudget, check_geo, _default_resolver, GeoMismatchError, BudgetExhausted
from pushframe.reconcile import apply_reconciliation, find_placeholders
from pushframe.sync import scan_directory, compute_plan, execute_plan, ConsecutiveWriteFailureError
from pushframe.utils.settings import (
    AURA_WRITE_BUDGET_CAPACITY,
    AURA_WRITE_BUDGET_REFILL_PER_MIN,
    AURA_WRITE_BUDGET_WAIT,
    AURA_WRITE_BUDGET_MAX_WAIT,
    AURA_COUNTRY,
    AURA_GEO_FAIL_OPEN,
    AURA_STATE_DIR,
)

# execute_plan's own defaults for the two budget-wait knobs (pushframe/sync.py:
# wait_on_budget=True, max_wait_seconds=3600.0). Used below to forward the
# AURA_WRITE_BUDGET_WAIT / AURA_WRITE_BUDGET_MAX_WAIT env values only when they
# would actually change execute_plan's behavior, preserving the Phase 08
# "defaults don't override execute_plan defaults" contract.
_EXECUTE_PLAN_DEFAULT_WAIT = True
_EXECUTE_PLAN_DEFAULT_MAX_WAIT = 3600.0

# First-N photos printed by default before truncating with a "+K more"
# summary line (D-06). Claude's discretion per 06-CONTEXT.md; real frames
# can hold 77+ assets (Phase 2 finding) so dumping everything by default
# isn't useful in a terminal.
INSPECT_PHOTO_LIMIT = 10


def build_parser() -> argparse.ArgumentParser:
    """Construct the root `pushframe` parser. Subparsers are structured so
    `inspect`/`sync` siblings can be added in later phases (D-02).

    `--debug` lives on the root parser (folded todo, promoted from the
    `status`-only subparser) so it is parsed once regardless of which
    subcommand runs, and every future subcommand inherits the same
    quiet-by-default logging convention for free.
    """
    parser = argparse.ArgumentParser(prog='pushframe')
    # REL-02 (phase 22): version reporting from the single __init__ constant
    # (pyproject is asserted equal by tests/test_version.py + the release
    # workflow against the git tag).
    from pushframe import __version__
    parser.add_argument('--version', action='version',
                        version=f'pushframe {__version__}')
    parser.add_argument(
        '--debug',
        action='store_true',
        default=False,
        help='Show verbose loguru request/response logging on stderr',
    )
    subparsers = parser.add_subparsers(dest='command', required=True)
    subparsers.add_parser('status', help='Check config/auth health and list account frames')
    subparsers.add_parser('logout', help='Delete the stored session token (email and settings are kept)')
    doctor_parser = subparsers.add_parser(
        'doctor', help='Field write-probe: 1 tiny test image through the real '
                       'write path, then GO/NO-GO for a sync from this machine')
    doctor_parser.add_argument('--frame', default=None,
                               help='Target frame name (substring) or id — default: first frame')
    doctor_parser.add_argument('--no-write', action='store_true', default=False,
                               help='Session/frames checks only (no test image written)')
    doctor_parser.add_argument('--debug', action='store_true', default=False)
    config_parser = subparsers.add_parser(
        'config', help='Setup wizard and config family — run `pushframe config --help` for the subcommand map')
    # argparse_known: config_args may itself contain --flags (pair add
    # --album A --frame F); parse_known_args would otherwise reject them as
    # unknown top-level options.
    config_parser.add_argument('config_args', nargs='*', metavar='args',
                               help='show | import [--file F] | set <k> <v> | '
                                    'get <k> | path | pair add/remove/list')
    inspect_parser = subparsers.add_parser('inspect', help="Inspect a frame's photos and metadata")
    inspect_parser.add_argument('--frame', required=True, help='Frame name (substring) or id')
    sync_parser = subparsers.add_parser('sync', help='Dry-run diff a local directory against a frame')
    sync_parser.add_argument('dir', help='Local directory to scan for photos')
    sync_parser.add_argument('--frame', required=True, help='Frame name (substring) or id')
    sync_parser.add_argument('--apply', action='store_true', default=False, help='Execute the plan (upload + delete) instead of only printing it')
    sync_parser.add_argument('--yes', action='store_true', default=False, help='Skip the confirmation prompt (required for --apply when running non-interactively)')
    # Scheduled sync-dir jobs (pushframe schedule add ... --sync-dir) run
    # through this verb, so it needs the same bookkeeping flags as scheduled
    # google-sync. (Regression: units passed --scheduled before this parser
    # accepted it -- every sync-dir tick died 'unrecognized arguments'.)
    sync_parser.add_argument('--scheduled', action='store_true', default=False,
                             help='Timed-run bookkeeping: the job log opens with a '
                                  'timestamped "=== run ... ===" header and closes with '
                                  'the rc/elapsed footer (used by pushframe schedule units)')
    sync_parser.add_argument('--report-tag', default=None, dest='report_tag',
                             help='Job identity in the log header and the log-rotation '
                                  'target (schedule units pass the job name; sync jobs '
                                  'have no --report email)')
    # The three tiers of "no longer in the local directory" (D-02/D-03).
    # Hiding is the default because the frame has no photo-count limit, so a
    # mistaken sync should cost visibility, never photos. argparse enforces
    # the mutual exclusion at parse time (V5).
    removal_group = sync_parser.add_mutually_exclusive_group()
    removal_group.add_argument('--delete', action='store_true', default=False,
                               help='Remove gone-local photos from the frame instead of hiding them (frame-scoped; the photo leaves this frame)')
    removal_group.add_argument('--hard-delete', action='store_true', default=False, dest='hard_delete',
                               help='IRREVERSIBLY destroy gone-local photos instead of hiding them (account-wide; requires typing the exact count to confirm)')

    # `push` = purely additive upload from a supply ("buffet") directory. Unlike
    # `sync`, the frame is NOT diffed-to-match the directory: nothing is ever
    # deleted (structurally -- to_delete is forced empty). Photos already on the
    # frame are still skipped via the md5 diff, so only new files upload. The
    # probe flags (--limit/--batch-size/--chunk-delay) make it the safe tool for
    # empirically measuring the anti-abuse write budget without touching
    # existing frame photos.
    push_parser = subparsers.add_parser('push', help='Upload photos from a directory to a frame (additive -- never deletes)')
    push_parser.add_argument('dir', help='Local directory of photos to upload (a supply/"buffet"; the frame is NOT synced to match it)')
    push_parser.add_argument('--frame', required=True, help='Frame name (substring) or id')
    push_parser.add_argument('--apply', action='store_true', default=False, help='Execute the upload instead of only printing the plan')
    push_parser.add_argument('--yes', action='store_true', default=False, help='Skip the confirmation prompt (required for --apply when running non-interactively)')
    push_parser.add_argument('--limit', type=int, default=None, help='Upload at most N photos this run (for controlled anti-abuse budget probing)')
    push_parser.add_argument('--batch-size', type=int, default=None, dest='batch_size', help='Assets per select_asset/batch_update call (default 50)')
    push_parser.add_argument('--chunk-delay', type=float, default=None, dest='chunk_delay', help='Seconds to pause between write chunks (default 5)')
    # Proactive write-rate-budget + geo pre-flight guard overrides (Phase 09,
    # ANTI-06). `sync` deliberately does NOT expose these -- `sync --apply`
    # still gets the budget/geo guard by default (built from AURA_* env vars
    # in run_sync), just without per-run override flags this phase.
    push_parser.add_argument('--max-wait', type=float, default=None, dest='max_wait', help='Max seconds to wait for write budget before stopping (default 3600)')
    push_parser.add_argument('--no-wait', action='store_true', default=False, help='Stop immediately instead of waiting when the write budget is exhausted')
    push_parser.add_argument('--country', default=None, help='Override the expected account country for the geo pre-flight guard (default from PUSHFRAME_COUNTRY, legacy AURA_COUNTRY)')
    push_parser.add_argument('--ignore-budget', action='store_true', default=False, dest='ignore_budget', help='Escape hatch: bypass the write budget entirely for this run')

    # `reconcile` = data hygiene on EXISTING stuck placeholder rows (REL-05,
    # D-13) -- deliberately outside the sync/push loop. Report-only by
    # default; `--remove` is required to attempt any write, mirroring
    # `--apply`'s report-vs-mutate split.
    reconcile_parser = subparsers.add_parser(
        'reconcile', help='Report (and optionally remove) stuck placeholder rows on a frame')
    reconcile_parser.add_argument('--frame', required=True, help='Frame name (substring) or id')
    reconcile_parser.add_argument(
        '--remove', action='store_true', default=False,
        help='Attempt removal of stuck placeholder rows instead of only reporting them')
    reconcile_parser.add_argument(
        '--yes', action='store_true', default=False,
        help='Skip the confirmation prompt (required for --remove when running non-interactively)')
    reconcile_parser.add_argument(
        '--mechanism', choices=['remove', 'hard-delete', 'complete'], default='remove',
        help="Which removal mechanism to attempt. 'remove' (the default) is confirmed working "
             "live; 'hard-delete' is unconfirmed; 'complete' is not yet implemented")
    reconcile_parser.add_argument(
        '--max-age-hours', type=float, default=24.0, dest='max_age_hours',
        help='Minimum age in hours for a placeholder row to be reported as stuck rather than '
             'recently created (default 24)')
    # Plan 11-06, Task 1: explicit opt-in on `find_placeholders`'
    # `unknown_age_policy` -- corrects D-15's unconditional form now that
    # plan 11-05 established live that `created_at` is never sent by this
    # API, which made the unconditional form permanently inert rather than
    # conservative (see pushframe/reconcile.py's find_placeholders
    # docstring). Bare `--remove` (this flag omitted) is BYTE-FOR-BYTE
    # unchanged: an unresolvable creation time still lands in unknown_age
    # and is never a removal candidate. Keyword-only on the Python side and
    # its own explicitly named flag here -- nothing promotes a row by
    # accident.
    reconcile_parser.add_argument(
        '--include-unknown-age', action='store_true', default=False, dest='include_unknown_age',
        help='Explicit opt-in: treat placeholder rows whose creation time this API never sends '
             '(unknown_age) as eligible for removal too, not just rows old enough per '
             '--max-age-hours. Without this flag, --remove cannot touch unknown-age rows.')

    # Phase 17 (LGS-02..05): Google link + album selection. google-link is
    # BOTH the link and the re-link command (one documented surface); the
    # dedicated-profile env prerequisite is enforced in run_google_link.
    link_parser = subparsers.add_parser(
        'google-link', help='Link (or re-link) Google Photos via the dedicated-profile browser bootstrap')
    album_parser = subparsers.add_parser(
        'google-album', help='Select a Google Photos album and enumerate every item with exact disk weight')
    album_parser.add_argument(
        'target', nargs='?', default=None,
        help='Album share URL, AF1Qip… id, or album-name substring (ambiguity -> numbered list, exit 2)')
    album_parser.add_argument(
        '--list', action='store_true', default=False,
        help='List the account\'s shared albums and exit (discovery aid)')
    album_parser.add_argument(
        '--verbose', action='store_true', default=False,
        help='Also print the per-item table (one line per photo)')
    # Phase 18 (CSE-01..08, SAFE-01..04): album → frame mirror sync. Dry-run
    # is the structural default; --apply is gated (SAFE-02 mass-hide threshold
    # + y/N) and removal is hide-only (CSE-06) — no delete tier on this verb.
    gsync_parser = subparsers.add_parser(
        'google-sync', help='Mirror a Google Photos album onto one frame (dry-run by default; hide-by-default removals)')
    gsync_parser.add_argument(
        'album', nargs='?', default=None,
        help='Album share URL, AF1Qip… id, or album-name substring '
             '(omit when using --pair or --all — the config supplies it)')
    gsync_parser.add_argument(
        '--frame', default=None,
        help='Target frame name substring or id (single album→frame pair). '
             'Use --all instead to run every configured pair')
    gsync_parser.add_argument(
        '--all', action='store_true', default=False, dest='all_pairs',
        help='Run every configured pair (album/frame come from config; state '
             'shards per pair; ONE shared write budget caps the total)')
    gsync_parser.add_argument(
        '--pair', default=None,
        help='Run one named pair (album/frame from the config store)')
    gsync_parser.add_argument(
        '--scheduled', action='store_true', default=False,
        help='Timed-run semantics: a plan hiding too many photos (over the '
             'mass-hide safety threshold) SKIPS AND LOGS '
             'instead of proceeding (used by pushframe schedule units)')
    gsync_parser.add_argument(
        '--report', default=None,
        help='Email a run report. DEBUG: every run (full output + the run\'s '
             'log tail — precise traces when something is not quite '
             'right); INFO: every run (the summary — frames synced, photos, '
             'actions); ERROR: only when a run failed or carries a '
             'potential problem (silence means healthy). Transport '
             '(one-time): `pushframe schedule report --to ... --smtp-host '
             '...`')
    gsync_parser.add_argument(
        '--report-to', default=None, dest='report_to',
        help='Override the configured report recipient')
    gsync_parser.add_argument(
        '--report-tag', default=None, dest='report_tag',
        help='Label in the report subject (schedule units pass the job name)')
    sched_parser = subparsers.add_parser(
        'schedule', help='Install/list/remove systemd USER timers '
                         '(no root; runs from stored config, never prompts)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'WHAT GETS SCHEDULED\n'
            '  --pair NAME     a named album<->frame pair: the timer\n'
            '                  runs `google-sync "<album>" --frame "<frame>" --apply\n'
            '                  --yes --scheduled` — the Google Photos album is mirrored\n'
            '                  onto the frame at each tick (the flagship flow).\n'
            '                  NAME is the label YOU chose at install time:\n'
            '                  `pushframe config pair add <name> --album A --frame F`;\n'
            '                  list existing names with `pushframe config pair list`.\n'
            '                  An unknown name is refused (the error lists the known\n'
            '                  ones). No pair yet? --album/--frame works inline.\n'
            '  --album A --frame F       same google-sync run, without a named pair.\n'
            '  --sync-dir DIR --frame F  run the local-directory `sync` instead.\n'
            '  --at "OnCalendar"         explicit systemd calendar (e.g.\n'
            '                  "Mon *-*-* 02:00") instead of --every Nmin|Nh|Nd.\n'
            '\n'
            'EMAIL REPORTS (scheduled google-sync jobs can email their run report)\n'
            '  Two halves: `schedule report` configures WHERE the emails go (a\n'
            '  one-time transport setup); `--report LEVEL` on the job decides\n'
            '  WHICH runs email and how much detail.\n'
            '  pushframe schedule report --to you@example.com --smtp-host smtp.example.com\n'
            '                  [--smtp-port 587] [--smtp-user U]   one-time setup\n'
            '  pushframe schedule report --test    send a trial email now\n'
            '  --report DEBUG    every run: full output + the run\'s log tail\n'
            '                    (precise traces when something is not quite right)\n'
            '  --report INFO     every run: the summary (frames synced, photos, actions)\n'
            '  --report ERROR    only when a run failed or carries a potential\n'
            '                    problem — silence means healthy\n'
            '\n'
            'EXAMPLES\n'
            '  pushframe schedule add nightly --pair family --every 1d --report INFO\n'
            '  pushframe schedule add hourly --sync-dir /srv/photos --frame Salon --every 1h\n'
            '  pushframe schedule list   |   pushframe schedule remove nightly\n'
            '\n'
            'Timers are systemd USER units (~/.config/systemd/user/pushframe-*.timer) —\n'
            'no root. Headless host? `loginctl enable-linger $USER` once. Logs:\n'
            '~/.local/state/pushframe/<job>.log'),
    )
    sched_parser.add_argument('schedule_args', nargs='*', metavar='args',
                              help='add | list | remove <job> (details and '
                                   'examples below)')
    gsync_parser.add_argument(
        '--apply', action='store_true', default=False,
        help='Execute the plan (uploads + hides). Without it, only print the plan')
    gsync_parser.add_argument(
        '--yes', action='store_true', default=False,
        help='Skip the confirmation prompt (required for --apply when running non-interactively)')
    gsync_parser.add_argument(
        '--debug', action='store_true', default=False,
        help='Verbose logging')
    gsync_parser.add_argument(
        '--batch-size', type=int, default=None, dest='batch_size',
        help='Assets per select_asset/batch_update call (default 50). '
             'Lower it (e.g. 10) if the anti-abuse layer trips on batch volume '
             '— a 1-item write may pass where a 50-item chunk is refused')
    return parser


def _configure_cli_logging(debug: bool) -> None:
    """Neutralize (or leave alone) loguru's stderr handlers for the CLI.

    `Aura.__init__` -> `Aura._init_logger()` (frozen per D-04) adds a
    level=INFO stderr sink on every construction but never removes loguru's
    auto-registered default stderr handler (its `logger.remove()` is
    commented out), so two stderr handlers fire on every HTTP call. Because
    `aura.py` cannot be edited, this CLI-side helper re-initializes loguru's
    sinks *after* `Aura()` construction to compensate for the frozen file's
    missing cleanup.

    - debug=True: no-op — every sink `_init_logger()` registered stays
      active, reproducing today's full verbose output (opt-in per the
      user's UAT suggestion).
    - debug=False (default): drop every accumulated handler, then restore
      on-disk logging (the same `logs/file_{time}.log` sink target
      `_init_logger()` uses) plus a stricter `sys.stderr` sink at level
      WARNING so genuine warnings/errors still surface without the
      INFO/DEBUG request/response spam.
    """
    if debug:
        return

    logger.remove()
    os.makedirs('logs/', exist_ok=True)
    logger.add('logs/file_{time}.log')
    logger.add(sys.stderr, level='WARNING')


from pushframe import config_store
from pushframe.utils import settings


def _wizard_login(email: str, password: str) -> dict:
    """Live login for the config wizard (D-03). Returns
    {email, auth_token, user_id, frames} on success; raises on bad
    credentials. Module-level seam: tests patch this, never the network.

    Aura.login returns the Aura itself (not the user); the session facts
    are attached to the instance. Phase 23 fixed this seam — it used to
    read `user.auth_token` off the returned Aura, which would have crashed
    the first REAL wizard login (mocks hid it)."""
    from pushframe.aura import Aura
    aura = Aura()
    aura.login(email=email, password=password)
    frames = []
    try:
        frames = [{'name': f.name, 'id': f.id} for f in aura.frame_api.get_frames()]
    except Exception:
        pass  # frame listing is optional at setup time
    return {'email': email, 'auth_token': aura.auth_token,
            'user_id': aura.user_id, 'frames': frames}


def _print_config_help() -> None:
    """`pushframe config --help` — a one-screen map of the config family.

    The CLI passthrough routes EVERYTHING after `config` into run_config,
    so argparse never sees `--help` for this verb; this is the hand-rolled
    replacement (kept in sync with docs/CLI.md's config section).
    """
    print(
        'usage: pushframe config [SUBCOMMAND [ARGS]]\n'
        '\n'
        'With no subcommand: the interactive setup wizard — asks for the Aura\n'
        'credentials (login-tested before anything is written), then the\n'
        'default frame. Run it once after install; re-run any time, Enter\n'
        'keeps stored values.\n'
        '\n'
        'Subcommands:\n'
        '  show              Effective settings, secrets redacted, with each\n'
        "                    value's source (env / file / default)\n"
        '  path              Print the config file path\n'
        '  import [--file F] Adopt a .env file (default: .env) as config\n'
        '  set KEY VALUE     Store one setting (validated; e.g.\n'
        '                    config set DEFAULT_FRAME "Living Room") —\n'
        '                    `config set --help` explains every key\n'
        '  get KEY           Print one setting and where it comes from\n'
        '  pair list         List named album→frame pairs\n'
        '  pair add NAME --album A --frame F\n'
        '                    Save a pair (consumed by google-sync --pair / --all)\n'
        '  pair remove NAME  Remove a named pair\n'
        '\n'
        'Examples:\n'
        '  pushframe config            first-time setup\n'
        '  pushframe config show       what is set, and from where\n'
        '  pushframe config set DEFAULT_FRAME "Living Room"\n'
        '  pushframe config pair add family --album "Album X" --frame "Living Room"\n'
        '\n'
        'Full reference: '
        'https://github.com/coredmp95/pushframe/blob/master/docs/CLI.md'
    )


def _print_pair_help() -> None:
    """`pushframe config pair --help` — what a pair IS, then how to use it.

    A bare mapping list explained nothing to a first-time operator (the
    "pairing system is not easy to understand" report, 2026-10-02); this
    spells out the one-time naming step and the commands that consume it.
    """
    print(
        'usage: pushframe config pair list\n'
        '       pushframe config pair add <name> --album ALBUM --frame FRAME\n'
        '       pushframe config pair remove <name>\n'
        '\n'
        'A pair is a named album↔frame mapping stored in the config file.\n'
        'Name it once, then every command can refer to it by name instead\n'
        'of retyping the album and the frame:\n'
        '\n'
        '  pushframe config pair add family --album "Album X" --frame "Living Room"\n'
        '  pushframe google-sync --pair family              # dry-run plan\n'
        '  pushframe google-sync --pair family --apply      # mirror it\n'
        '  pushframe google-sync --all                      # every pair, sorted\n'
        '  pushframe schedule add nightly --pair family --every 1d\n'
        '\n'
        'With --pair/--all the album and frame come from the config — the\n'
        'positional album argument is not needed. Each pair keeps its own\n'
        'sync state (manifest + cache) under its name, so pairs never\n'
        'interfere; `pair list` shows the mappings.\n'
        '\n'
        'Full reference: https://github.com/coredmp95/pushframe/blob/master/'
        'docs/CLI.md#pairs--one-album--several-frames-and-back'
    )


def run_config(wizard_args=None, stdin_isatty: bool | None = None) -> int:
    """pushframe config — the command family (CFG-01..04).

    No args: the interactive wizard (credentials first, login-tested,
    nothing written on failure — D-03). Subcommands: show, import,
    set/get/path (CFG-02/03/04). """
    import getpass
    import sys as _sys

    args = list(wizard_args if wizard_args is not None else [])
    is_tty = _sys.stdin.isatty() if stdin_isatty is None else stdin_isatty

    # `--help` never reaches argparse (the CLI passthrough captures
    # everything after `config`), so it used to fall through into the
    # wizard — or, non-interactively, answered "needs an interactive
    # terminal". Answer it here; and refuse unknown subcommands/flags
    # instead of silently launching an interactive flow (`config shwo`
    # used to start the wizard).
    if args and args[0] in ('--help', '-h', 'help'):
        _print_config_help()
        return 0
    if args and args[0] not in ('pair', 'show', 'path', 'import', 'set', 'get'):
        print(f'unknown config subcommand: {args[0]!r}\n')
        _print_config_help()
        return 2

    # --- subcommands -------------------------------------------------------
    if args and args[0] == 'pair':
        # Phase 25 (MTF-01): the named-pair store (add/remove/list).
        from pushframe import pairs as pairs_mod
        sub = args[1:]
        if sub and sub[0] in ('--help', '-h', 'help'):
            _print_pair_help()
            return 0
        if not sub or sub[0] == 'list':
            pairs_mod.pair_list()
            return 0
        if sub[0] == 'add':
            if len(sub) > 1 and sub[1] in ('-h', '--help'):
                _print_pair_help()
                return 0
            rc, name, album, frame = _parse_pair_add(sub)
            if rc != 0:
                return rc
            try:
                pairs_mod.pair_add(name, album=album, frame=frame)
            except Exception as e:
                print(f'pair not added: {e}')
                return 1
            m, c = pairs_mod.pair_state_paths(name)
            print(f'pair "{name}" saved (album "{album}" → frame "{frame}")')
            print(f'  state: {m.parent} (+ cache {c})')
            print(f'  run it: pushframe google-sync --pair {name} [--apply] '
                  f'(or `schedule add nightly --pair {name} --every 1d`)')
            return 0
        if sub[0] == 'remove' and len(sub) >= 2:
            if sub[1].startswith('--'):
                print('pair remove: needs a pair name — '
                      '`pushframe config pair list` shows the configured ones')
                return 2
            if len(sub) > 2:
                print(f'pair remove: unexpected "{sub[2]}" — '
                      'usage: pushframe config pair remove <name>')
                return 2
            if pairs_mod.pair_remove(sub[1]):
                print(f'pair "{sub[1]}" removed (its state files, if any, '
                      f'are left in place under pairs/{sub[1]}/ — delete '
                      f'them manually if you want a clean slate)')
            else:
                print(f'no pair named "{sub[1]}".')
            return 0
        print('usage: pushframe config pair list | pair add <name> --album A '
              '--frame F | pair remove <name>')
        print('run `pushframe config pair --help` for what a pair is and how '
              'to use it')
        return 2

    if args and args[0] == 'show':
        return _config_show()
    if args and args[0] == 'path':
        print(settings.CONFIG_PATH)
        return 0
    if args and args[0] == 'import':
        return _config_import(args[1:])
    if args and args[0] == 'set':
        argv = args[1:]
        if argv and argv[0] in ('--help', '-h', 'help'):
            _print_set_help()
            return 0
        if argv and argv[0] in _REPORT_KEY_ALIASES:
            return _config_report_set(argv[0], argv[1:])
        return _config_set(argv)
    if args and args[0] == 'get':
        if len(args) < 2:
            print('usage: pushframe config get <key>')
            return 1
        if len(args) > 2:
            print(f'config get: unexpected "{args[2]}" — '
                  'usage: pushframe config get <key>')
            return 2
        name = args[1]
        if name in _REPORT_KEY_ALIASES:
            from pushframe import report as report_mod
            rcfg = report_mod.load_config()
            env_var = {'report_to': 'PUSHFRAME_REPORT_TO',
                       'smtp_host': 'PUSHFRAME_SMTP_HOST',
                       'smtp_port': 'PUSHFRAME_SMTP_PORT',
                       'smtp_user': 'PUSHFRAME_SMTP_USER',
                       'smtp_password': 'PUSHFRAME_SMTP_PASSWORD',
                       'report_from': 'PUSHFRAME_SMTP_FROM'}[name]
            if os.getenv(env_var) is not None:
                shown = '***' if name == 'smtp_password' else os.getenv(env_var)
                print(f'{name} = {shown!r}  (source: env)')
                return 0
            stored = (config_store.load().get('report', {}) or {}).get(
                _REPORT_KEY_ALIASES[name])
            shown = '***' if name == 'smtp_password' and stored else stored
            source = 'file' if stored else 'default'
            print(f'{name} = {shown!r}  (source: {source})')
            return 0
        if name not in settings.known_keys() and name not in ('email', 'default_frame', 'debug'):
            print(f'unknown key {name!r}. Run `pushframe config set --help` '
                  f'for every key, explained. Known settings: '
                  f'{", ".join(sorted(settings.known_keys()))}, or report '
                  f'transport keys: {", ".join(sorted(_REPORT_KEY_ALIASES))}')
            return 1
        value = config_store.setting(name)
        source = 'file' if value is not None else ('env' if settings.shadowed_keys({name: None}) else 'default')
        print(f'{name} = {value!r}  (source: {source})')
        return 0

    # --- the wizard --------------------------------------------------------
    if not is_tty:
        print('pushframe config: the wizard needs an interactive terminal '
              '(use config set / config import for scripts).')
        return 1

    # Identity provisioning (5.1.1, venus anti-abuse lesson): before the
    # FIRST API call of a fresh install, give it a unique device id so its
    # very first login already presents a credible per-install fingerprint
    # instead of the all-zeros id every pushframe install used to share.
    # Only the absence of one triggers a write; an explicit setting (env or
    # file) is never second-guessed. The block contains no account data —
    # D-03's "nothing written before a successful login" keeps its meaning
    # for session facts (email/token/frame/debug).
    if not os.getenv('PUSHFRAME_DEVICE_IDENTIFIER') \
            and not os.getenv('AURA_DEVICE_IDENTIFIER') \
            and config_store.setting('DEVICE_IDENTIFIER') is None:
        import uuid
        # load→mutate→save (NOT update(settings=...), which would REPLACE the
        # settings map and drop any sibling file settings like LOCALE).
        data = config_store.load()
        identity = str(uuid.uuid4())
        data.setdefault('settings', {})['DEVICE_IDENTIFIER'] = identity
        config_store.save(data)
        print(f'device identity provisioned: DEVICE_IDENTIFIER = {identity}'
              ' (unique to this install)')

    existing = config_store.load()
    if existing.get('email'):
        print(f"configuring pushframe (current email: {existing['email']} — Enter keeps it)")
    else:
        print('configuring pushframe — credentials are login-tested now, stored after.')
    email = input('Aura email: ').strip()
    if not email and existing.get('email'):
        # The banner promised "Enter keeps it" — keep that promise. With a
        # stored token this path finishes with ZERO API calls (anti-abuse:
        # a gratuitous login is exactly what the trip punishes).
        email = existing['email']
    if not email:
        print('no email given — aborting, nothing written.')
        return 1
    if existing.get('email') == email and existing.get('auth_token'):
        keep = input('Keep the stored session token instead of re-entering the password? [Y/n] ')
        if keep.strip().lower() in ('', 'y', 'yes'):
            _finish_wizard(existing)
            return 0
    password = getpass.getpass('Aura password (input hidden): ')

    # D-03: the live login happens BEFORE anything is written.
    try:
        result = _wizard_login(email, password)
    except Exception as e:
        print(f'login failed — NOTHING was written: {e}')
        return 1

    data = {
        'email': email,
        'auth_token': result['auth_token'],
        'debug': existing.get('debug', False),
    }
    if result.get('user_id'):
        data['user_id'] = result['user_id']
    frames = result.get('frames') or []
    if frames:
        print('frames on the account:')
        for i, f in enumerate(frames, 1):
            print(f'  {i}. {f["name"]}')
        choice = input('default frame number (Enter to skip): ').strip()
        # Prompt-contract audit fix: an out-of-range or non-numeric answer
        # used to be silently treated as a skip — re-ask until a valid
        # number or an explicit Enter.
        while choice and not (choice.isdigit() and 1 <= int(choice) <= len(frames)):
            choice = input(f'invalid choice, pick 1-{len(frames)} or Enter to skip: ').strip()
        if choice:
            data['default_frame'] = frames[int(choice) - 1]['name']
    debug = input('enable debug logging by default? [y/N] ').strip().lower()
    if debug in ('y', 'yes'):
        data['debug'] = True

    shadowed = settings.shadowed_keys(data)
    config_store.update(**data)
    print(f'config saved: {settings.CONFIG_PATH} (0600)')
    if shadowed:
        print('WARNING: these keys are currently overridden by environment '
              f'variables (env > file): {", ".join(shadowed)} — the file '
              'value will NOT take effect until the env var is unset.')
    return 0


def _finish_wizard(data: dict) -> None:
    frames_note = data.get('default_frame')
    print(f'config saved: {settings.CONFIG_PATH} (0600)'
          + (f' (default frame: {frames_note})' if frames_note else ''))


# The report/SMTP keys `config set/get` accept directly (stored under the
# config's `report` key; PUSHFRAME_SMTP_* / PUSHFRAME_REPORT_TO override at
# use time — see pushframe/report.py). Names are operator-facing; the
# config-file spellings differ for two of them.
_REPORT_KEY_ALIASES = {
    'smtp_host': 'smtp_host',
    'smtp_port': 'smtp_port',
    'smtp_user': 'smtp_user',
    'smtp_password': 'smtp_password',
    'report_to': 'to',
    'report_from': 'from',
}


def _config_show() -> int:
    """CFG-02: EVERY configuration surface with its status, secrets
    redacted, sources labeled.

    An inventory is only useful if it is exhaustive (2026-10-02 report:
    "how do you know a setting exists?" — the report transport used to be
    invisible until configured, the wizard keys absent until set, and
    pairs were never mentioned). Everything the tool reads is listed:
    the settings table, the wizard keys, the six report keys, and the
    pairs — each with its effective value/source or `(not set)`.
    """
    data = config_store.load()
    print('effective pushframe configuration (env var > config file > default):')
    for name in settings.known_keys():
        value = getattr(settings, name)
        env_names = settings.DEFAULTS[name]['env']
        if any(os.getenv(e) is not None for e in env_names):
            source = 'env'
        elif config_store.setting(name) is not None:
            source = 'file'
        else:
            source = 'default'
        print(f'  {name} = {value!r}  ({source})')
    # Wizard-managed keys: always listed, `(not set)` included — a missing
    # line is how a setting stays undiscoverable.
    for key in ('email', 'default_frame', 'debug'):
        if data.get(key) is not None:
            print(f'  {key} = {data[key]!r}  (file)')
        elif key == 'debug':
            print('  debug = False  (default)')
        else:
            print(f'  {key} = (not set)')
    if data.get('auth_token'):
        print('  auth_token = ***  (file)')
    else:
        print('  auth_token = (not set)')
    # The report transport (report.py's load_config): env overrides the
    # stored `report` block; the password never prints. All six keys are
    # ALWAYS listed.
    from pushframe import report as report_mod
    rdata = data.get('report', {}) or {}
    print('  email reports (google-sync --report):')
    env_of = {
        'report_to': 'PUSHFRAME_REPORT_TO',
        'smtp_host': 'PUSHFRAME_SMTP_HOST',
        'smtp_port': 'PUSHFRAME_SMTP_PORT',
        'smtp_user': 'PUSHFRAME_SMTP_USER',
        'smtp_password': 'PUSHFRAME_SMTP_PASSWORD',
        'report_from': 'PUSHFRAME_SMTP_FROM',
    }
    configured = False
    for op_name, env_var in env_of.items():
        stored_key = _REPORT_KEY_ALIASES[op_name]
        env_value = os.getenv(env_var)
        if env_value is not None:
            shown = '***' if op_name == 'smtp_password' else env_value
            print(f'    {op_name} = {shown!r}  (env)')
            configured = True
        elif rdata.get(stored_key):
            shown = '***' if op_name == 'smtp_password' else rdata[stored_key]
            print(f'    {op_name} = {shown!r}  (file)')
            configured = True
        elif op_name == 'smtp_port':
            print('    smtp_port = (not set — 587 by default; 465 = SSL)')
        else:
            print(f'    {op_name} = (not set)')
    if configured:
        print('    (trial: `pushframe schedule report --test`; levels go on '
              'each job with `schedule add … --report DEBUG|INFO|ERROR`)')
    else:
        print('    (not configured — `pushframe config set report_to '
              'you@example.com` + `config set smtp_host smtp.example.com`, '
              'then `pushframe schedule report --test`)')
    # Pairs: count and names here, the full mapping with `config pair list`.
    pairs = data.get('pairs', {}) or {}
    if pairs:
        print(f'  pairs: {len(pairs)} ({", ".join(pairs)}) — detail: '
              f'pushframe config pair list')
    else:
        print('  pairs: none — add with `pushframe config pair add <name> '
              '--album A --frame F`')
    # Identity hygiene (5.1.1, venus anti-abuse lesson): the all-zeros id is
    # shared by every unprovisioned pushframe install and reads as a
    # non-phone client — name it and hand the remedy.
    if settings.DEVICE_IDENTIFIER == settings.DEFAULTS['DEVICE_IDENTIFIER']['default']:
        print('  WARNING: DEVICE_IDENTIFIER is still the shared all-zeros '
              'default — pushd reads it as a non-phone client (venus '
              '2026-09-30: reads stayed green while writes were 401-refused '
              'for months). Give this install a unique identity: '
              'pushframe config set DEVICE_IDENTIFIER "$(uuidgen)"')
    return 0


def _config_import(argv: list[str]) -> int:
    """CFG-03: migrate an .env into the config file — storing only keys the
    environment does NOT already resolve (never store what env provides)."""
    import json as _json
    # The same ONE strict tail parser as schedule add / pair add / report
    # (nameless mode): a malformed tail is a named usage error (exit 2) —
    # a dangling `--file` used to fall back to importing `.env` silently,
    # unknown tokens were ignored, and the first of two `--file` flags won
    # without a word.
    rc, _name, opts = _parse_flag_value_tail(
        argv, prefix='config import', flags=('--file',),
        usage='usage: pushframe config import [--file FILE]',
        next_cmd='pushframe config --help', next_note='for the config family',
        name_label=None)
    if rc != 0:
        return rc
    path = Path(opts.get('--file') or '.env')
    if not path.exists():
        print(f'config import: {path} not found.')
        return 1
    mapping = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        mapping[key.strip()] = value.strip().strip('"').strip("'")
    stored, skipped = {}, {}
    for raw_key, value in mapping.items():
        name = None
        stripped = raw_key.removeprefix('PUSHFRAME_').removeprefix('AURA_')
        for candidate in (raw_key, 'AURA_' + stripped, stripped):
            if candidate in settings.DEFAULTS:
                name = candidate
                break
        if name is None:
            # keep the documented settings spelling in the error
            name = raw_key
            skipped[raw_key] = 'unknown setting'
            continue
        if settings.shadowed_keys({name: value}):
            skipped[raw_key] = 'already resolved from env'
            continue
        stored[name] = settings._cast(name, value)
    if stored:
        config_store.update(settings=stored)
    for raw_key, reason in skipped.items():
        print(f'  skipped {raw_key}: {reason}')
    for name in stored:
        print(f'  stored {name} (file)')
    if not stored and not skipped:
        print('nothing to import.')
    return 0


def _print_set_help() -> None:
    """`pushframe config set --help` — every accepted key, explained."""
    print(
        'usage: pushframe config set KEY VALUE\n'
        '\n'
        'Keys are grouped: the settings table, the wizard-managed keys, and\n'
        'the email-report transport. Precedence is always environment →\n'
        'config file → default; `config show` labels each value\'s source.\n'
        '\n'
        'Wizard-managed (config set refuses email/auth_token — the wizard\n'
        'login-tests them; default_frame/debug are accepted):\n'
        '  default_frame   Frame name preselected in prompts and used as the\n'
        '                  --frame default when a verb allows omitting it\n'
        '  debug           True/False — verbose request/response logging by default\n'
        '\n'
        'Email-report transport — WHERE the report emails go (consumed by\n'
        '`schedule add … --report`, which decides WHICH runs email;\n'
        'PUSHFRAME_REPORT_TO / PUSHFRAME_SMTP_* override at use time):\n'
        '  report_to       Recipient address of the run reports\n'
        '  smtp_host       SMTP relay hostname (e.g. smtp.example.com)\n'
        '  smtp_port       SMTP port — 587 STARTTLS (default), 465 SSL\n'
        '  smtp_user       Login for relays that require authentication\n'
        '  smtp_password   Its password (stored 0600, printed as ***)\n'
        '  report_from     From address (defaults to smtp_user)\n'
        '\n'
        'Anti-abuse / write budget (token bucket paced before any write):\n'
        '  AURA_COUNTRY    Your account\'s country code (e.g. FR) — enables\n'
        '                  the geo pre-flight; unset skips the check\n'
        '  AURA_GEO_FAIL_OPEN        true/false — on a country-lookup failure,\n'
        '                            continue (true) or block (false)\n'
        '  AURA_WRITE_BUDGET_CAPACITY     Burst size in write requests (30)\n'
        '  AURA_WRITE_BUDGET_REFILL_PER_MIN  Refill per minute (0.75)\n'
        '  AURA_WRITE_BUDGET_WAIT     true/false — wait for refill (true)\n'
        '                            or stop when the bucket is dry\n'
        '  AURA_WRITE_BUDGET_MAX_WAIT Max seconds a run will wait (3600)\n'
        '\n'
        'Client identity (what the API sees — read docs/CLI.md "Client\n'
        'identity" before touching):\n'
        '  DEVICE_IDENTIFIER  Unique id per install — MUST be changed from\n'
        '                     the shared all-zeros default: config set\n'
        '                     DEVICE_IDENTIFIER "$(uuidgen)"\n'
        '  USER_AGENT         App version string presented to the API\n'
        '  LOCALE             Locale presented to the API (en-US)\n'
        '  AURA_APP_IDENTIFIER  App bundle id presented to the API\n'
        '\n'
        'Advanced / endpoint overrides (leave alone unless you know why):\n'
        '  AURA_API_BASE_URL, AURA_API_VERSION, AURA_STATE_DIR,\n'
        '  IMAGE_PROXY_BASE_URL, AWS_S3_BUCKET, AWS_UPLOAD_IDENTITY_POOL_ID,\n'
        '  AWS_SQS_IDENTITY_POOL_ID\n'
        '\n'
        'Unknown keys are rejected with the list of known ones. Full per-key\n'
        'reference: '
        'https://github.com/coredmp95/pushframe/blob/master/docs/CLI.md'
    )


def _config_report_set(op_name: str, argv: list[str]) -> int:
    """`config set <report-key> <value>` — store under the config's `report`
    block (the same block `schedule report --to ...` writes)."""
    if not argv:
        print(f'usage: pushframe config set {op_name} <value>')
        return 1
    if len(argv) > 1:
        print(f'config set {op_name}: unexpected "{argv[1]}" — '
              f'usage: pushframe config set {op_name} <value>')
        return 2
    if argv[0].startswith('--'):
        print(f'config set {op_name}: "{argv[0]}" looks like a flag, not a '
              'value — set takes exactly KEY VALUE')
        return 2
    from pushframe import config_store
    data = config_store.load()
    stored = data.get('report', {}) or {}
    stored[_REPORT_KEY_ALIASES[op_name]] = argv[0]
    data['report'] = stored
    config_store.save(data)
    shown = '***' if op_name == 'smtp_password' else argv[0]
    print(f'{op_name} = {shown!r} written to {settings.CONFIG_PATH} '
          f'(report block; PUSHFRAME_{op_name.upper()} still wins if set)')
    print('next: `pushframe schedule report --test` sends a trial email; '
          '`schedule add … --report DEBUG|INFO|ERROR` turns reports on per job.')
    return 0


def _config_set(argv: list[str]) -> int:
    if len(argv) < 2:
        print('usage: pushframe config set <key> <value>')
        print('run `pushframe config set --help` for every key, explained')
        return 1
    name, value = argv[0], argv[1]
    if name.startswith('--'):
        print(f'config set: the key "{name}" starts with "--" — '
              'that looks like a flag, not a key (set takes exactly '
              'KEY VALUE)')
        return 2
    if len(argv) > 2:
        print(f'config set: unexpected "{argv[2]}" — '
              'usage: pushframe config set <key> <value>')
        return 2
    if value.startswith('--'):
        print(f'config set: the value for {name!r} starts with "--" — '
              'that looks like a flag, not a value (set takes exactly '
              'KEY VALUE)')
        return 2
    if name in ('email', 'auth_token'):
        print(f'{name!r} is managed by the wizard: run `pushframe config`.')
        return 1
    if name not in settings.DEFAULTS:
        print(f'unknown key {name!r}. Run `pushframe config set --help` for '
              f'every key, explained. Known settings: '
              f'{", ".join(sorted(settings.known_keys()))}')
        return 1
    data = config_store.load()
    data.setdefault('settings', {})[name] = value  # stored raw; cast on resolve
    config_store.save(data)
    print(f'{name} = {value!r} written to {settings.CONFIG_PATH} (env var still wins if set — {name} shadowing: {bool(settings.shadowed_keys({name: value}))})')
    return 0


def run_logout() -> int:
    """pushframe logout (phase 24, SEC-03, D-02): delete the stored TOKEN
    and only the token. Email, settings, default_frame (and future pairs)
    survive; the file stays 0600; idempotent; token material never printed."""
    from pushframe import config_store
    stored = config_store.load()
    if not (stored.get('auth_token') or stored.get('user_id')):  # noqa: S105
        print('no stored session — nothing to remove.')
        return 0
    config_store.update(auth_token=None, user_id=None)
    who = stored.get('email') or 'the account'
    print(f'logged out: the stored session for {who} was deleted.')
    print('kept: your email and settings. To sign back in: pushframe config')
    return 0


def run_status(aura=None, debug: bool = False, google_session=None) -> int:
    """Status command handler. Returns a process exit code (0 success, 1
    failure) — never calls sys.exit directly. Accepts an optional injected
    `Aura` (dependency-injection seam) so this is testable offline.

    `google_session` (phase 17) is the same DI pattern for the Google
    section: inject a GoogleSession (real one over a MockTransport in
    tests) to control its output hermetically. When None, the section is
    built from the cookie vault — vault absent means `linked: no` with no
    network call.
    """
    # Config health check (D-07) — must run first. Name WHERE the
    # credentials come from instead of raw env-var presence: a stored-
    # session host (the common `pushframe config` install) is fully
    # working with no PUSHFRAME_* variables at all, and the old
    # "PUSHFRAME_EMAIL: NOT SET" lines read like a fault there. Same
    # source vocabulary as `config show` (env / file). Never a secret.
    # IDN-04: PUSHFRAME_* primary, AURA_* legacy fallback.
    email_set = bool(os.getenv('PUSHFRAME_EMAIL') or os.getenv('AURA_EMAIL'))
    password_set = bool(os.getenv('PUSHFRAME_PASSWORD') or os.getenv('AURA_PASSWORD'))
    if email_set and password_set:
        # env path (roadmap §23 criterion 2: env overrides the file) — a
        # real login with the env credentials.
        print('Config: environment (PUSHFRAME_EMAIL/PUSHFRAME_PASSWORD)')
        session = ('env', os.getenv('PUSHFRAME_EMAIL') or os.getenv('AURA_EMAIL'))
    else:
        # stored-session path (phase 23, criterion 1): no password-bearing
        # env → resume the session the config wizard established. NO login
        # call happens on this path (phase 24 generalizes it to all verbs).
        try:
            stored = config_store.load()
        except ValueError as e:
            print(f'config file is corrupt and no PUSHFRAME_EMAIL/PASSWORD '
                  f'is set — fix or remove {settings.CONFIG_PATH}: {e}')
            return 1
        if not (stored.get('email') and stored.get('auth_token')):
            # D-09: stop immediately — no Aura, no network call, when
            # nothing usable is configured.
            print('no credentials: set PUSHFRAME_EMAIL/PUSHFRAME_PASSWORD '
                  'or run `pushframe config`')
            return 1
        print(f'Config: stored session ({Path(settings.CONFIG_PATH)} — '
              f'created by `pushframe config`, no password needed)')
        session = ('stored', stored)

    aura = aura or Aura()
    # Must run after Aura() construction (which registers the noisy sinks)
    # and before login/get_frames (the HTTP calls that trigger them).
    _configure_cli_logging(debug)

    stored = None  # the token-refresh seam only exists on the stored path
    if session[0] == 'env':
        try:
            aura.login()
        except Exception as e:
            # D-08: bad credentials, network error, or API drift all surface
            # here — a broad catch at the CLI boundary is correct.
            print(f'Login failed: {e}')
            return 1
        who = session[1]
    else:
        stored = session[1]
        aura.resume_session(email=stored['email'],
                            auth_token=stored['auth_token'],
                            user_id=stored.get('user_id'))
        who = stored['email']

    # The frames read is the FIRST authenticated call of the token path —
    # its failure modes are operator-relevant and must never traceback
    # (PRF-02). Key lesson of 2026-09-30 (debug/status-crash-401-trip): on
    # a READ, the trip-shaped logout:true body means a STALE STORED TOKEN
    # first — a fresh login read frames fine minutes later — so the remedy
    # is ONE TTY re-login right here (venus: the env path proved a fresh
    # login reads green); the 24h wait is only the answer if a FRESH token
    # is refused too. Never more than ONE refresh attempt per run, never a
    # prompt off-TTY (scheduled runs fail named instead).
    frames = _read_frames_with_refresh(aura, who, stored)
    if frames is None:
        return 1  # named failure already printed by the helper
    print(f'Logged in as {who}')
    print(f'{len(frames)} frames:')
    for frame in frames:
        print(f'  - {frame.name} (id: {frame.id})')

    # Phase 17 (LGS-03): the Google link section — email + session state
    # only, NEVER cookie values or vault contents (D-02).
    for line in _google_status_section(google_session):
        print(line)

    # Scheduled jobs: WHAT runs (the album→frame it touches) and WHEN the
    # next tick fires — an operator answering "what does this machine do
    # on its own?" should not need a second command (2026-10-02 report).
    for line in _schedule_status_section():
        print(line)

    return 0


def _schedule_status_section() -> list[str]:
    """The `Scheduled:` section for `status` — one line per pushframe timer.

    For each timer the paired SERVICE unit's ExecStart is parsed for the
    verb and its target (google-sync --pair X / --all / album + --frame /
    sync dir + frame), so the line names what flows where, not just a unit
    name. Next fire time comes from `systemctl list-timers`. Everything
    degrades to an honest '(unreadable)' — status never fails over this
    section, and nothing here triggers a network call.
    """
    from pushframe import schedule as sch
    lines = ['Scheduled:']
    try:
        timers_out, _ = sch.run_systemctl(
            'list-timers', '--all', 'pushframe-*.timer', '--no-pager')
    except Exception:
        return lines + ['  (systemctl --user unavailable — no schedule info)']

    # list-timers rows: NEXT(weekday date time tz) LEFT LAST PASSED UNIT
    # ACTIVATES — the NEXT cell is 4 tokens ("Sat 2026-10-03 00:03:19 CEST").
    next_fire: dict[str, str] = {}
    for row in timers_out.splitlines():
        parts = row.split()
        if len(parts) >= 8 and 'pushframe-' in row and '.timer' in row:
            unit = next(p for p in parts if p.endswith('.timer'))
            unit_idx = parts.index(unit)
            next_fire[unit] = ' '.join(parts[:unit_idx - 3])

    unit_dir = sch.UNIT_DIR
    services = sorted(unit_dir.glob('pushframe-*.service')) \
        if unit_dir.is_dir() else []
    if not services:
        return lines + ['  none — add one with `pushframe schedule add '
                        '<job> --pair <name> --every 1d`']
    for svc_path in services:
        job = svc_path.stem.replace('pushframe-', '')
        what = '(unreadable unit)'
        exec_line = ''
        try:
            unit_text = svc_path.read_text(encoding='utf-8')
            exec_line = next((l for l in unit_text.splitlines()
                              if l.startswith('ExecStart=')), '')
            what = _describe_exec_start(exec_line)
        except Exception:
            pass
        when = next_fire.get(f'pushframe-{job}.timer', '(timer not enabled)')
        lines.append(f'  {job}: {what} — next: {when}')
        if exec_line:
            try:
                problem = _unit_argv_parse_problem(exec_line)
            except Exception:
                problem = None  # status degrades; it never fails here
            if problem:
                lines.append(f'    ⚠ would FAIL to start: {problem} — '
                             f're-add the job (`pushframe schedule add ...`) '
                             f'or fix the unit')
    lines.append('  (detail: `pushframe schedule list`)')
    return lines


def _unit_argv_parse_problem(exec_line: str) -> str | None:
    """Would this unit's ExecStart survive the CURRENT binary's argparse?

    The tokens from the verb onward go through the real parser
    (parse_known_args): unrecognized flags, missing required arguments or
    invalid values surface here as a one-line reason — None when the
    command line is accepted verbatim. This is the guard against the
    sync-dir class of breakage (units installed passing a flag the parser
    of the day rejects die on EVERY tick — argparse exit 2, before any
    sync — with nothing but journalctl to explain why): hand-edited units
    and version drift (unit newer or older than the binary) are exactly
    what it surfaces. Never raises — status degrades, it does not fail.
    """
    import contextlib
    import io
    import shlex
    body = exec_line.removeprefix('ExecStart=')
    try:
        tokens = shlex.split(body)
    except ValueError:
        return 'ExecStart is not shell-quoted correctly'
    parser = build_parser()
    try:
        sub = next(a for a in parser._actions if a.dest == 'command')
        verbs = set(sub.choices)
    except Exception:
        return None  # cannot introspect the parser — refuse to guess
    verb_idx = next((i for i, t in enumerate(tokens) if t in verbs), None)
    if verb_idx is None:
        return 'no pushframe command found in ExecStart'
    argv = tokens[verb_idx:]
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            _, extras = parser.parse_known_args(argv)
    except SystemExit:
        detail = err.getvalue().split('error:', 1)[-1]
        detail = ' '.join(detail.split())[:160]
        return f'argparse refused: {detail}' if detail \
            else 'argparse refused the command line'
    if extras:
        return f'unrecognized arguments: {" ".join(map(str, extras))[:160]}'
    return None


def _frame_flag_needs_value(rest: list[str], flag: str) -> bool:
    """True when `flag` is present but dangling (last token, or followed by
    another --flag) — the ExecStart parser's guard against indexing past
    the end of a hand-written unit."""
    if flag not in rest:
        return False
    j = rest.index(flag)
    return j + 1 >= len(rest) or rest[j + 1].startswith('--')


def _describe_exec_start(exec_line: str) -> str:
    """Human summary of an ExecStart= line: verb + what flows where."""
    import shlex
    body = exec_line.removeprefix('ExecStart=')
    try:
        tokens = shlex.split(body)
    except ValueError:
        return '(unreadable unit)'
    # tokens[0] is the executable (absolute since 2026-10-02); find the verb
    verb_idx = next((i for i, t in enumerate(tokens)
                     if t in ('google-sync', 'sync', 'push')), None)
    if verb_idx is None:
        return '(unknown verb)'
    verb = tokens[verb_idx]
    rest = tokens[verb_idx + 1:]
    if verb == 'google-sync':
        pair = rest[rest.index('--pair') + 1] \
            if '--pair' in rest and not _frame_flag_needs_value(rest,
                                                                '--pair') \
            else None
        if pair:
            return f'mirror album→frame for pair "{pair}"'
        if '--all' in rest:
            return 'mirror every configured pair'
        album = rest[0] if rest and not rest[0].startswith('-') else '?'
        frame = rest[rest.index('--frame') + 1] \
            if '--frame' in rest \
            and not _frame_flag_needs_value(rest, '--frame') else '?'
        return f'mirror album "{album}" → frame "{frame}"'
    if verb == 'sync':
        src = rest[0] if rest and not rest[0].startswith('-') else '?'
        frame = rest[rest.index('--frame') + 1] \
            if '--frame' in rest \
            and not _frame_flag_needs_value(rest, '--frame') else '?'
        return f'sync local "{src}" → frame "{frame}"'
    return f'{verb} (scheduled)'


def _read_frames_with_refresh(aura, who, stored):
    """Thin wrapper over the shared gate (session.frames_read_with_refresh)
    — status, inspect, sync/push and google-sync all read frames through
    the same named-failure + one-shot-refresh contract."""
    from pushframe.session import frames_read_with_refresh
    return frames_read_with_refresh(aura, who, stored)


def _google_status_section(google_session=None) -> list[str]:
    """Build the `Google:` section lines for `status` (LGS-03/D-02).

    linked/account/session only — never a cookie value, a token, or the
    vault contents. With no injected session: a missing vault prints
    `linked: no` with zero network calls; a present vault gets ONE home
    GET (short timeout) for the usable/expired signal, and any failure
    degrades to an honest `unreachable` instead of a guess.
    """
    lines = ['Google:']
    if google_session is not None:
        session = google_session
    else:
        try:
            # Denylist-safe route: vault.load() fires inside from_vault,
            # whose frame is pushframe.google.client (sanctioned).
            session = GoogleSession.from_vault(timeout=10.0)
        except CookieVaultError:
            lines.append('  linked: no')
            lines.append('  (no Google session vault — run `pushframe google-link`)')
            return lines
    try:
        linked = session.is_linked()
    except Exception as e:
        lines.append('  linked: unknown (session check failed)')
        lines.append(f'  session: unreachable ({type(e).__name__})')
        return lines
    lines.append(f'  linked: {"yes" if linked else "no"}')
    if linked:
        email = None
        try:
            email = session.account_email()
        except Exception:
            pass
        lines.append(f'  account: {email or "(not resolvable)"}')
        lines.append('  session: usable')
    else:
        lines.append('  account: (not resolvable)')
        lines.append('  session: expired')
    return lines


def run_google_link(*, debug: bool = False, bootstrap_fn=None) -> int:
    """google-link command handler (LGS-02): link AND re-link are the same
    command. Requires the dedicated-profile env (T-16-06 posture) and runs
    the interactive bootstrap through the `bootstrap_fn` seam — tests inject
    a fake and never launch a browser (TEST-02).

    Prints the vault path (path only), the 0600 confirmation and the
    auth-marker cookie NAMES — never values (T-16-07/D-02).
    """
    _configure_cli_logging(debug)

    # v5.1: no env prerequisite here — bootstrap._require_profile() owns the
    # precedence (override > legacy > built-in default, created on demand)
    # and raises a helpful BootstrapError (near-miss names included) that the
    # handler below prints verbatim.

    # Re-link notice (LGS-02): same command refreshes an existing session.
    # Vault read routes through from_vault (denylist-sanctioned); a dead or
    # absent vault both fall through to the bootstrap.
    try:
        GoogleSession.from_vault()
        print('existing Google session found — refreshing it (re-link is this same command)')
    except Exception:
        pass

    bootstrap = bootstrap_fn or default_bootstrap
    try:
        summary = bootstrap()
    except BootstrapError as e:
        print(f'google-link failed: {e}')
        return 1

    print(f"vault saved: {summary['vault_path']} (0600, outside the repo)")
    print(f"session cookies present: {summary['cookie_count']} total; "
          f"auth markers: {summary['auth_markers']}")
    return 0


@dataclass
class FrameResolution:
    """Result of resolving a `--frame` CLI argument against the account's
    frame list (CLI-04). `status` is a discriminator — 'resolved',
    'ambiguous', or 'not_found' — deliberately not a raised exception
    (MOD-03 typed exceptions stays deferred; see 06-CONTEXT.md)."""
    frame: Frame | None
    status: str
    candidates: list[Frame] = field(default_factory=list)


def resolve_frame(target: str, frames: list[Frame]) -> FrameResolution:
    """Resolve a `--frame` value to a single Frame (CLI-04, D-01..D-04).

    Pure function — no I/O, no side effects. Resolution order:
    1. Case-insensitive substring match on `Frame.name` (D-01).
       - Exactly one match -> resolved (D-02).
       - More than one match -> ambiguous; the id fallback is NOT
         attempted (D-03).
    2. Zero name matches -> fall back to an exact (case-sensitive)
       match on `Frame.id`.
       - Exactly one match -> resolved (D-02).
       - Otherwise -> not_found, candidates is every frame on the
         account so the caller can list available names (D-04).
    """
    target_lower = target.lower()
    name_matches = [f for f in frames if target_lower in f.name.lower()]

    if len(name_matches) == 1:
        return FrameResolution(frame=name_matches[0], status='resolved', candidates=[])
    if len(name_matches) > 1:
        return FrameResolution(frame=None, status='ambiguous', candidates=name_matches)

    id_matches = [f for f in frames if f.id == target]
    if len(id_matches) == 1:
        return FrameResolution(frame=id_matches[0], status='resolved', candidates=[])

    return FrameResolution(frame=None, status='not_found', candidates=frames)


@dataclass
class AlbumResolution:
    """Result of resolving a `google-album <target>` argument (phase 17,
    D-05) — mirrors resolve_frame's FrameResolution contract: a 'resolved'/
    'ambiguous'/'not_found' discriminator plus candidates, deliberately not
    a raised exception. `album` is an AlbumSummary when resolved.

    Direct targets (share URL or AF1Qip id) bypass name resolution entirely
    (D-05: "Lien/ID direct accepté tel quel") — they resolve by construction
    with zero candidates, and the enumeration validates them."""

    album: 'AlbumSummary | None'
    status: str
    candidates: list = field(default_factory=list)


def resolve_album(target: str, albums: list) -> AlbumResolution:
    """Resolve a `google-album` target (D-05, resolve_frame-style).

    Pure function — no I/O. Order:
    1. A target that LOOKS like a share URL (photos.google.com/share/…,
       photos.app.goo.gl/…) or a full AF1Qip… id resolves directly — no
       name matching (D-05's link/id path).
    2. Otherwise: case-insensitive substring match on album titles —
       exactly one -> resolved; several -> ambiguous (numbered choice is
       the caller's print; no silent pick, T-17-06).
    3. Zero matches -> not_found with every album as candidates.
    """
    import re as _re

    share_id_m = _re.search(r"/share/([A-Za-z0-9_-]+)", target)
    if ('photos.google.com/share/' in target or 'photos.app.goo.gl/' in target
            or _re.fullmatch(r"AF1Qip[A-Za-z0-9_-]{20,}", target)):
        album_id = share_id_m.group(1) if share_id_m else target
        return AlbumResolution(album=AlbumSummary(album_id=album_id, title=None,
                                                  share_url=target),
                               status='resolved', candidates=[])

    target_lower = target.lower()
    name_matches = [a for a in albums
                    if a.title and target_lower in a.title.lower()]
    if len(name_matches) == 1:
        return AlbumResolution(album=name_matches[0], status='resolved', candidates=[])
    if len(name_matches) > 1:
        return AlbumResolution(album=None, status='ambiguous', candidates=name_matches)
    return AlbumResolution(album=None, status='not_found', candidates=albums)


def _print_album_candidates(candidates: list, numbered: bool = False,
                            show_count: bool = False) -> None:
    """Print an album candidate list (redacted ids, D-05/T-17-08); the
    metadata item count is shown when available (item_count is metadata —
    videos included; the authoritative photo count is the enumeration's)."""
    for i, candidate in enumerate(candidates, 1):
        label = f'  {i}. ' if numbered else '  - '
        title = candidate.title or '(untitled)'
        id_shape = redact_link(candidate.album_id) if candidate.album_id else '(no id)'
        count = ''
        if show_count and candidate.item_count is not None:
            count = f' — {candidate.item_count} items (metadata)'
        print(f'{label}{title} (id shape: {id_shape}){count}')


def run_google_album(target: str, *, debug: bool = False, session=None,
                     list_all: bool = False, verbose: bool = False) -> int:
    """google-album command handler (LGS-04/LGS-05, D-05/D-06): resolve an
    album by share URL, id, or title substring — ambiguity prints a numbered
    list and exits 2 (no silent pick, no per-photo picking anywhere) — then
    enumerate EVERY item (continuation until exhaustion) and print the exact
    disk weight (1-byte Range GETs).

    Default output is a summary (item count, page/exhaustion state, total
    weight with min/max/avg); `--verbose` additionally prints the per-item
    table — for a large album that is hundreds of opaque id lines an
    operator almost never wants.

    `session` is the DI seam (TEST-02): tests inject a GoogleSession over a
    MockTransport; without injection the session comes from the vault.
    """
    _configure_cli_logging(debug)

    if target is None and not list_all:
        print("google-album: provide an album name, share URL, or id "
              "(or pass --list to discover the account's shared albums)")
        return 2

    if session is None:
        try:
            session = GoogleSession.from_vault()
        except CookieVaultError as e:
            print(f'google-album failed: {e}')
            return 1

    try:
        albums = list_shared_albums(session)
    except EnumerateError as e:
        print(f'google-album failed: {redact_tokens(str(e))}')
        return 1

    if list_all:
        if not albums:
            print('No shared albums found on this account.')
            return 0
        print(f'{len(albums)} shared albums:')
        _print_album_candidates(albums, numbered=True, show_count=True)
        return 0

    resolved = resolve_album(target, albums)

    if resolved.status == 'ambiguous':
        print(f"'{target}' matches more than one album — re-run with a link/id "
              f"or a fuller name:")
        _print_album_candidates(resolved.candidates, numbered=True, show_count=True)
        return 2
    if resolved.status == 'not_found':
        print(f"No album matches '{target}'. Available shared albums:")
        _print_album_candidates(albums, numbered=True, show_count=True)
        return 2

    album = resolved.album
    # The page_key (share URL's ?key=) is optional but the /albums listing
    # carries it — use it when the resolved album matches a listed one.
    page_key = None
    if album.share_url and 'key=' in album.share_url:
        page_key = album.share_url.split('key=', 1)[1].split('&', 1)[0] or None
    elif album.album_id:
        listed = next((a for a in albums if a.album_id == album.album_id
                       and a.share_url and 'key=' in a.share_url), None)
        if listed:
            page_key = listed.share_url.split('key=', 1)[1].split('&', 1)[0] or None

    print(f'Album: {album.title or "(untitled)"} '
          f'(id shape: {redact_link(album.album_id)})')

    try:
        listing = enumerate_album(session, album.album_id, page_key=page_key)
    except EnumerateError as e:
        print(f'google-album failed: {redact_tokens(str(e))}')
        return 1

    base_urls = [i['base_url'] for i in listing.items]
    sizes = measure_disk_weight(session, base_urls)
    for item, size in zip(listing.items, sizes):
        item['bytes'] = size
    total = sum(sizes)

    print(f'Items: {len(listing.items)} '
          f"(pages: {listing.page_count}, "
          f"exhausted: {'cleanly' if listing.exhausted_cleanly else 'NO — INCOMPLETE'})")
    print(f'Disk weight: {total:,} bytes = {total / 1024 / 1024:.1f} MiB '
          f'(min {min(sizes):,}, max {max(sizes):,}, '
          f'avg {total // max(len(sizes), 1):,})')
    if verbose:
        print('Per-item (index | id shape | WxH | bytes):')
        for idx, (item, size) in enumerate(zip(listing.items, sizes), 1):
            w = item.get('width') if item.get('width') is not None else '?'
            h = item.get('height') if item.get('height') is not None else '?'
            id_shape = redact_link(item['id'])
            print(f'  {idx:4d} | {id_shape} | {w}x{h} | {size:,}')
    else:
        print('(per-item detail: re-run with --verbose)')

    return 0


def run_inspect(frame_arg: str, aura=None, debug: bool = False) -> int:
    """Inspect command handler. Returns a process exit code (0 success, 1
    failure) — never calls sys.exit directly. Accepts an optional injected
    `Aura` (dependency-injection seam) so this is testable offline.

    `frame_arg` is argparse-required, not an env var, so (unlike
    `run_status`) there's no config-precheck step before constructing
    `Aura()`.
    """
    # Must run after Aura() construction (which registers the noisy sinks)
    # and before login/get_frames (the HTTP calls that trigger them).
    _configure_cli_logging(debug)
    from pushframe.session import establish_session, SessionError
    try:
        if aura is None:  # DI contract: an injected Aura manages its own auth
            aura = establish_session(aura=aura)
    except SessionError as e:
        print(f'not authenticated: {e}')
        return 1
    except Exception as e:
        # D-05: bad credentials, network error, or API drift all surface
        # here — a broad catch at the CLI boundary is correct.
        print(f'Login failed: {e}')
        return 1

    from pushframe.session import auto_refresh_stored
    stored = auto_refresh_stored()
    frames = _read_frames_with_refresh(
        aura, who=(stored or {}).get('email')
        or os.getenv('PUSHFRAME_EMAIL') or os.getenv('AURA_EMAIL'),
        stored=stored)
    if frames is None:
        return 1
    try:
        resolved = resolve_frame(frame_arg, frames)

        if resolved.status == 'ambiguous':
            print(f"'{frame_arg}' matches more than one frame name — re-run with --frame <id>:")
            for candidate in resolved.candidates:
                print(f'  - {candidate.name} (id: {candidate.id})')
            return 1

        if resolved.status == 'not_found':
            print(f"No frame matches name or id '{frame_arg}'. Available frames:")
            for candidate in resolved.candidates:
                print(f'  - {candidate.name} (id: {candidate.id})')
            return 1

        frame, total_asset_count = aura.frame_api.get_frame(resolved.frame.id)
        contributors = frame.contributors or []

        print(f'Frame: {frame.name} (id: {frame.id})')
        print(f'Owner: {frame.user.name} <{frame.user.email}>')
        print(f'Contributors ({len(contributors)}):')
        for contributor in contributors:
            print(f'  - {contributor.name} <{contributor.email}>')
        print(f'Assets: {total_asset_count}')

        assets = aura.get_all_assets(resolved.frame.id)
        shown = assets[:INSPECT_PHOTO_LIMIT]
        print(f'Photos (showing {len(shown)} of {len(assets)}, API order):')
        for asset in shown:
            print(f'  - {asset.id} | {asset.file_name} | {asset.taken_at_dt}')
        remaining = len(assets) - len(shown)
        if remaining > 0:
            print(f'  ... +{remaining} more')
        # REL-05, D-13: computed via the exact same pure function
        # `run_reconcile` calls, so the two counts can never disagree.
        print(f'Placeholder rows: {find_placeholders(assets).placeholder_count} '
              f'(run `pushframe reconcile --frame ...` for detail)')
    except Exception as e:
        # WR-01 fail-loud (D-05): surface post-login API drift instead of a
        # raw traceback.
        print(f'Failed to inspect frame: {e}')
        return 1

    return 0


def run_reconcile(frame_arg: str, *, remove: bool = False, yes: bool = False, mechanism: str = 'remove',
                   max_age_hours: float = 24.0, include_unknown_age: bool = False,
                   aura=None, debug: bool = False) -> int:
    """Reconcile command handler (REL-05, D-13) -- data hygiene on EXISTING
    stuck placeholder rows, deliberately outside the sync/push loop. Reports
    how many placeholder rows a frame carries unconditionally, whether or
    not any removal mechanism works, and (only when `remove=True`) attempts
    a bounded, gated removal. Returns a process exit code (0 success, 1
    failure) -- never calls sys.exit directly. Accepts an optional injected
    `Aura` (dependency-injection seam), mirroring `run_inspect`.

    `include_unknown_age` (plan 11-06, Task 1, CLI-side `--include-unknown-age`)
    is the explicit opt-in on `find_placeholders`' `unknown_age_policy` --
    default `False` reproduces today's behaviour exactly (an unresolvable
    creation time stays in `unknown_age`, never a removal candidate).
    """
    # Must run after Aura() construction (which registers the noisy sinks)
    # and before login/get_frames (the HTTP calls that trigger them).
    _configure_cli_logging(debug)
    from pushframe.session import establish_session, SessionError
    try:
        if aura is None:  # DI contract: an injected Aura manages its own auth
            aura = establish_session(aura=aura)
    except SessionError as e:
        print(f'not authenticated: {e}')
        return 1
    except Exception as e:
        print(f'Login failed: {e}')
        return 1

    try:
        frames = aura.frame_api.get_frames()
        resolved = resolve_frame(frame_arg, frames)

        if resolved.status == 'ambiguous':
            print(f"'{frame_arg}' matches more than one frame name — re-run with --frame <id>:")
            for candidate in resolved.candidates:
                print(f'  - {candidate.name} (id: {candidate.id})')
            return 1

        if resolved.status == 'not_found':
            print(f"No frame matches name or id '{frame_arg}'. Available frames:")
            for candidate in resolved.candidates:
                print(f'  - {candidate.name} (id: {candidate.id})')
            return 1

        frame = resolved.frame
        assets = aura.get_all_assets(frame.id)

        if not assets:
            # T-11-11: an empty listing cannot be distinguished from a
            # frame with genuinely no placeholders -- refuse to report a
            # reassuring zero on no data.
            print(f'No assets returned for "{frame.name}" (id: {frame.id}) -- an empty asset '
                  f'listing cannot be distinguished from a frame with no placeholders. Refusing '
                  f'to report zero placeholders on no data.')
            return 1

        # REL-05, D-13: the exact same pure function `run_inspect` calls, so
        # the two counts can never disagree. `unknown_age_policy` defaults
        # to 'unknown_age' -- 'stuck' only when --include-unknown-age was
        # explicitly passed (plan 11-06, Task 1).
        unknown_age_policy = 'stuck' if include_unknown_age else 'unknown_age'
        result = find_placeholders(assets, age_threshold_seconds=max_age_hours * 3600,
                                    unknown_age_policy=unknown_age_policy)

        print(f'Frame: {frame.name} (id: {frame.id})')
        print(f'Assets scanned: {result.total_scanned}')
        print(f'Placeholder rows: {result.placeholder_count}')
        print(f'  stuck (older than {max_age_hours}h): {len(result.stuck)}')
        print(f'  recently created (may still be processing): {len(result.recently_created)}')
        print(f'  creation time unknown: {len(result.unknown_age)}')
        for asset in result.stuck:
            print(f'    - {asset.id}')

        if not remove:
            # D-13: reconcile without --remove performs no write of any
            # kind -- return here, before any write path is reachable.
            return 0

        # D-13/D-16: --remove continuation. Fail closed on a non-interactive
        # invocation missing --yes -- never block on input() forever, never
        # silently proceed (mirrors run_sync's identical check verbatim).
        if not yes and not sys.stdin.isatty():
            print('--remove requires --yes when running non-interactively')
            return 1

        if not yes:
            if mechanism == 'hard-delete':
                # Same escalated-friction gate as run_sync's hard_delete
                # branch: this primitive is irreversible and account-wide,
                # so a reworded y/N is too easy to answer reflexively.
                count = len(result.stuck)
                print(f'IRREVERSIBLE: {count} row(s) will be permanently destroyed '
                      f'account-wide via hard-delete. This cannot be undone.')
                answer = input(f'Verbatim to confirm — type the number of rows to hard-delete ({count}): ')
                if answer.strip() != str(count):
                    print('Aborted.')
                    return 0
            else:
                answer = input(f'About to attempt removal of {len(result.stuck)} stuck row(s) on '
                                f'"{frame.name}" (id: {frame.id}) using mechanism "{mechanism}". '
                                f'Proceed? [y/N] ')
                if answer.strip().lower() not in ('y', 'yes'):
                    print('Aborted.')
                    return 0

        # D-13: the same account-wide budget as sync/push -- reconcile has
        # no --ignore-budget escape hatch, so this is always False.
        from pushframe.session import account_email
        write_budget = _build_write_budget(account_email(), False)

        apply_reconciliation(result, aura, frame.id, mechanism=mechanism, budget=write_budget)

        print(f'Removed: {len(result.removed)} succeeded, {len(result.failed)} failed')
        for asset_id, err in result.failed:
            print(f'  ! {asset_id}: {err}')

        return 1 if result.failed else 0
    except RateLimitError as e:
        # Anti-abuse throttle/lockout mid-removal -- apply_reconciliation
        # aborted the batch rather than emitting N confusing per-item 401s.
        print(f'Aborted: {e}')
        return 1
    except GeoMismatchError as e:
        print(f'VPN/exit IP in {e.found}, account expects {e.expected} — switch your VPN and retry.')
        return 1
    except BudgetExhausted as e:
        minutes = e.wait_seconds / 60
        print(f'Write budget exhausted, come back in ~{minutes:.0f} min (or pass --no-wait / raise --max-wait).')
        return 1
    except Exception as e:
        # WR-01 fail-loud (D-05 convention): surface post-login API drift
        # instead of a raw traceback.
        print(f'Failed to reconcile frame: {e}')
        return 1

    return 0


# How each removal mode is named in the plan and in the run summary. The
# wording tracks the primitive that actually runs, so a report can never say
# "delete" on a run that hid, or vice versa (D-07).
_REMOVAL_VERB_PRESENT = {'hide': 'hide', 'delete': 'delete', 'hard_delete': 'hard-delete'}
_REMOVAL_VERB_PAST = {'hide': 'Hidden', 'delete': 'Removed', 'hard_delete': 'Hard-deleted'}


def _build_write_budget(email: str, ignore_budget: bool) -> 'WriteBudget | None':
    """Factory (Phase 09, ANTI-06) constructing the per-account `WriteBudget`
    at the CLI boundary, mirroring the S3Client()/SQSClient() construction
    site (`execute_plan` itself never constructs one). Returns `None` when
    `ignore_budget` is set (the `--ignore-budget` escape hatch) -- omitting
    `budget` entirely from `exec_kwargs` bypasses the guard.

    Only `sha1(email)[:12]` (a non-cryptographic filename-uniqueness hash,
    not a security boundary) is used for the state-file name -- the email
    itself is never persisted in the file body (T-09-02).
    """
    if ignore_budget:
        return None
    if not email:
        # venus 2026-09-30 (debug gsync-apply-budget-none-crash): a run with
        # no resolvable identity (no env, no stored email) previously died
        # HERE, on email.encode(), AFTER the operator confirmed the apply.
        # A budget is per-ACCOUNT state; with no account named there is
        # nothing to key — skip pacing rather than crash. Named debug
        # output on stderr keeps this observable, never silent.
        import sys as _sys
        print('write-budget: no account identity (env or stored session) '
              '— pacing skipped for this run', file=_sys.stderr)
        return None
    state_path = AURA_STATE_DIR / f'budget-{hashlib.sha1(email.encode()).hexdigest()[:12]}.json'
    return WriteBudget.load(
        state_path, capacity=AURA_WRITE_BUDGET_CAPACITY, refill_per_min=AURA_WRITE_BUDGET_REFILL_PER_MIN,
    )


def _build_geo_check(country_override: str | None):
    """Factory (Phase 09, ANTI-06) constructing the zero-arg `geo_check`
    closure at the CLI boundary. `country_override` (the `--country` flag)
    takes precedence over `AURA_COUNTRY`; when neither is set (falsy),
    returns `None` -- the geo guard is skipped entirely, per `check_geo`'s
    own falsy-`expected_country` contract."""
    country = country_override or AURA_COUNTRY
    if not country:
        return None
    return lambda: check_geo(country, resolver=_default_resolver, fail_open=AURA_GEO_FAIL_OPEN)


def run_sync(dir_arg: str, frame_arg: str, apply: bool = False, yes: bool = False, aura=None, debug: bool = False,
             no_delete: bool = False, limit: int = None, batch_size: int = None, chunk_delay: float = None,
             verb: str = 'sync', max_wait: float = None, no_wait: bool = False,
             country: str = None, ignore_budget: bool = False,
             removal_mode: str = 'hide') -> int:
    """Sync command handler (SYNC-01/SYNC-03/SYNC-04). Resolves the target
    frame, scans `dir_arg` locally, computes the upload/delete/unchanged plan
    via `pushframe.sync`, and prints a full (untruncated) report.

    Dry-run remains the default (`apply=False`): nothing is executed and this
    function returns after printing the plan, exactly as before Phase 8.

    When `apply=True`, after the plan is printed a confirmation gate runs
    (D-01/D-02/D-03/D-04) before `execute_plan()` (Phase 8 Plan 02) is called
    with real `S3Client()`/`SQSClient()` instances — the only place in this
    module that constructs them. A non-interactive invocation without `--yes`
    fails closed (D-03) rather than hanging on `input()`. On confirmation,
    `execute_plan()`'s result is printed as a separated upload/delete
    success/failure summary (D-10) and the exit code reflects any failure
    (SYNC-04).

    Returns a process exit code (0 success, 1 failure) — never calls
    sys.exit directly. Accepts an optional injected `Aura` (dependency-
    injection seam) so this is testable offline, mirroring `run_inspect`.
    """
    # Must run after Aura() construction (which registers the noisy sinks)
    # and before login/get_frames (the HTTP calls that trigger them).
    _configure_cli_logging(debug)

    verb_present = _REMOVAL_VERB_PRESENT[removal_mode]
    verb_past = _REMOVAL_VERB_PAST[removal_mode]

    # PRF-01/02, D-03 (phase 24): machine prerequisites BEFORE any network —
    # a nonexistent source dir is a named error without touching the API.
    from pushframe.preflight import require_source_dir, PreflightError
    try:
        require_source_dir(dir_arg)
    except PreflightError as e:
        print(f'sync failed: {e}')
        return 1

    from pushframe.session import establish_session, SessionError
    try:
        if aura is None:  # DI contract: an injected Aura manages its own auth
            aura = establish_session(aura=aura)
    except SessionError as e:
        print(f'not authenticated: {e}')
        return 1
    except RateLimitError as e:
        # Anti-abuse throttle/lockout escalated to reject login (the HTTP 475
        # seen in the select-asset-401-unauthorized session). Surface the
        # back-off message explicitly rather than as a generic login failure.
        print(f'Rate limited / locked out at login — {e}')
        return 1
    except Exception as e:
        # Fail-loud (D-05 convention): bad credentials, network error, or API
        # drift all surface here — a broad catch at the CLI boundary is correct.
        print(f'Login failed: {e}')
        return 1

    from pushframe.session import auto_refresh_stored
    stored = auto_refresh_stored()
    frames = _read_frames_with_refresh(
        aura, who=(stored or {}).get('email')
        or os.getenv('PUSHFRAME_EMAIL') or os.getenv('AURA_EMAIL'),
        stored=stored)
    if frames is None:
        return 1
    try:
        resolved = resolve_frame(frame_arg, frames)

        if resolved.status == 'ambiguous':
            print(f"'{frame_arg}' matches more than one frame name — re-run with --frame <id>:")
            for candidate in resolved.candidates:
                print(f'  - {candidate.name} (id: {candidate.id})')
            return 1

        if resolved.status == 'not_found':
            print(f"No frame matches name or id '{frame_arg}'. Available frames:")
            for candidate in resolved.candidates:
                print(f'  - {candidate.name} (id: {candidate.id})')
            return 1

        frame = resolved.frame
        assets = aura.get_all_assets(frame.id)

        # WR-02: scanned separately from the surrounding API calls so a
        # local filesystem error (missing/invalid `dir_arg`, permission
        # error reading a file) is reported distinctly from a remote
        # API/auth failure instead of being collapsed into the same
        try:
            scan = scan_directory(Path(dir_arg))
        except OSError as e:
            print(f'Failed to scan {dir_arg}: {e}')
            return 1

        plan = compute_plan(scan.local_hashes, assets, scan.skipped_non_image)

        # Opt-in probe/push affordances. Defaults (no_delete=False, limit=None)
        # leave the classic `sync` behaviour byte-identical. `no_delete` (always
        # on for the `push` verb) makes the run purely additive -- it clears
        # to_delete so no existing frame photo can ever be removed, the safe
        # primitive for pushing from a "buffet" supply directory. `limit` caps
        # how many uploads are attempted, for controlled anti-abuse budget
        # probing. Both are applied BEFORE the plan is printed so the report
        # reflects exactly what will run.
        if no_delete:
            # `push` is upload-only: it must not hide, remove, OR re-show
            # anything. A re-show is still a visibility mutation of existing
            # frame photos, so it is cleared alongside the removals.
            plan.to_delete = []
            plan.to_reshow = []
        if limit is not None:
            plan.to_upload = sorted(plan.to_upload)[:limit]

        if verb == 'push':
            print(f'Push plan for {frame.name} (id: {frame.id}) — additive (no deletes), DRY RUN, nothing will be changed')
        else:
            print(f'Sync plan for {frame.name} (id: {frame.id}) — DRY RUN, nothing will be changed')
        print(f'To upload: {len(plan.to_upload)}')
        if no_delete:
            print('To delete: 0 (additive mode — existing frame photos left untouched)')
        else:
            # Name the verb that will actually run, so the plan can never read
            # "delete" on a run that hides (or vice versa) -- D-07.
            print(f'To {verb_present}: {len(plan.to_delete)}')
            # Re-shows get their own line rather than folding into unchanged:
            # they are a write, and the user should see it coming (D-08).
            print(f'To re-show: {len(plan.to_reshow)}')
        print(f'Unchanged: {plan.unchanged}')
        if plan.already_hidden:
            print(f'Already hidden: {plan.already_hidden} (no action needed)')

        # WR-03: local_hashes (and to_upload built from it) is populated in
        # filesystem-traversal order, which is OS/filesystem dependent and
        # not sorted -- sort here so dry-run output is reproducible across
        # runs/machines (e.g. diffable, stable for bug reports).
        for path in sorted(plan.to_upload):
            print(f'  + {path}')

        for asset in plan.to_delete:
            print(f'  - {asset.id} (taken {asset.taken_at_dt})')

        for asset in plan.to_reshow:
            print(f'  ~ {asset.id} (taken {asset.taken_at_dt}) — re-show')

        if plan.skipped_non_image > 0:
            print(f'{plan.skipped_non_image} non-photo files skipped')

        if plan.frame_no_hash > 0:
            print(f'{plan.frame_no_hash} frame assets without a content hash (e.g. videos) left untouched')

        if not apply:
            return 0

        # D-03: fail closed on a non-interactive invocation missing --yes --
        # never block on input() forever, never silently proceed.
        if not yes and not sys.stdin.isatty():
            print('--apply requires --yes when running non-interactively')
            return 1

        # D-01/D-02/D-04: a single confirmation gate covers the whole plan
        # (uploads + deletes together), echoing the resolved frame's name and
        # id so a substring --frame match can't silently apply to the wrong
        # frame.
        if not yes:
            if removal_mode == 'hard_delete':
                # A reworded y/N is too easy to answer reflexively for an
                # irreversible, account-wide destruction. Re-typing the exact
                # count forces the user to look at the number first (D-04).
                count = len(plan.to_delete)
                print(f'IRREVERSIBLE: {count} photo(s) will be permanently destroyed '
                      f'account-wide, not just removed from this frame. This cannot be undone.')
                answer = input(f'Verbatim to confirm — type the number of photos to hard-delete ({count}): ')
                if answer.strip() != str(count):
                    print('Aborted.')
                    return 0
            else:
                answer = input(f'About to apply this plan to "{frame.name}" (id: {frame.id}). Proceed? [y/N] ')
                if answer.strip().lower() not in ('y', 'yes'):
                    print('Aborted.')
                    return 0

        # Real AWS clients are constructed here only, on confirmed apply --
        # execute_plan() itself never constructs them (offline-testable seam
        # from Phase 8 Plan 02).
        s3_client = S3Client()
        sqs_client = SQSClient()

        # Proactive write-rate-budget + geo pre-flight guard (Phase 09,
        # ANTI-06) -- built here for BOTH verbs (push and sync both hit the
        # exact same anti-abuse surface), so `sync --apply` also gets
        # protection by default with zero new flags. Only `--ignore-budget`
        # (push-only) omits `budget`; `write_budget`/`geo_check` being
        # `None` is exec_kwargs's signal to omit the corresponding kwarg.
        from pushframe.session import account_email
        write_budget = _build_write_budget(account_email(), ignore_budget)
        geo_check = _build_geo_check(country)

        total = len(plan.to_upload) + len(plan.to_delete)
        # tqdm writes to stderr, so stdout-based test assertions (D-10's
        # summary, printed after the bar closes below) are unaffected.
        with tqdm(total=total, desc='Applying', unit='item') as bar:
            def _report_progress(kind, identifier, ok):
                bar.update(1)
                name = Path(identifier).name if kind == 'upload' else str(identifier)
                status = 'ok' if ok else 'FAIL'
                bar.set_postfix_str(f'{kind} {status} {name}')

            def _report_error(kind, identifier, ok, reason):
                # Phase 23.5 (venus): failures escalate WITH the cause and the
                # lockout remedy, live in the bar — not a bare FAIL.
                name = Path(identifier).name if kind == 'upload' else str(identifier)
                short = reason if len(reason) <= 120 else reason[:117] + '...'
                remedy = (' — likely account lockout: STOP, wait ~30 min, '
                          'then `pushframe status` to re-login') if '401' in short else ''
                bar.set_postfix_str(f'{kind} FAIL {name}: {short}{remedy}')

            def _report_wait(remaining):
                # Inter-chunk cooldown AND write-budget waits both arrive here
                # (execute_plan routes budget.acquire's on_wait through the
                # same callback) — one honest label: pacing is normal, the
                # bar is alive.
                bar.set_postfix_str(f'pacing {remaining:.0f}s — budget refill/cooldown, normal')

            # Only forward batch_size/chunk_delay when explicitly supplied so
            # execute_plan keeps its own defaults (WRITE_BATCH_SIZE /
            # WRITE_CHUNK_DELAY_SECONDS) otherwise -- no hardcoded values here.
            exec_kwargs = {}
            # execute_plan defaults to 'hide' too, so forwarding is always
            # safe -- but forward explicitly so the CLI's choice is the one
            # that runs, not a default that happens to agree.
            exec_kwargs['removal_mode'] = removal_mode
            if batch_size is not None:
                exec_kwargs['batch_size'] = batch_size
            if chunk_delay is not None:
                exec_kwargs['chunk_delay_seconds'] = chunk_delay
            # budget/geo_check forwarded whenever built (both verbs, by
            # default) -- omitted only when None (--ignore-budget, or no
            # AURA_COUNTRY/--country configured), preserving
            # execute_plan's own None defaults in that case. wait_on_budget/
            # max_wait_seconds forwarded ONLY when their override flag was
            # explicitly supplied, so a classic `sync --apply`/`push --apply`
            # with no new flags forwards nothing beyond the default guard
            # objects themselves (test_sync_defaults_do_not_override_execute_plan_defaults's guarantee).
            if write_budget is not None:
                exec_kwargs['budget'] = write_budget
            if geo_check is not None:
                exec_kwargs['geo_check'] = geo_check
            # WR-01: wire the AURA_WRITE_BUDGET_WAIT / AURA_WRITE_BUDGET_MAX_WAIT
            # env defaults into the guard so a user who configures them gets an
            # effect, with the per-run --no-wait / --max-wait flags taking
            # precedence over the env. Each kwarg is forwarded ONLY when it would
            # actually change execute_plan's own default (True / 3600.0) -- so a
            # run with neither an override flag nor a non-default env var still
            # forwards nothing, preserving the Phase 08 no-override contract.
            effective_wait = False if no_wait else AURA_WRITE_BUDGET_WAIT
            if effective_wait != _EXECUTE_PLAN_DEFAULT_WAIT:
                exec_kwargs['wait_on_budget'] = effective_wait
            effective_max_wait = max_wait if max_wait is not None else AURA_WRITE_BUDGET_MAX_WAIT
            if effective_max_wait != _EXECUTE_PLAN_DEFAULT_MAX_WAIT:
                exec_kwargs['max_wait_seconds'] = effective_max_wait

            result = execute_plan(
                plan, aura, frame.id, s3_client=s3_client, sqs_client=sqs_client,
                progress=_report_progress, on_wait=_report_wait,
                on_error=_report_error, **exec_kwargs,
            )

        # D-10: separated success/failure summary, each failed item named.
        print(f'Uploads: {result.upload_succeeded} succeeded, {len(result.upload_failures)} failed')
        for path, err in result.upload_failures:
            print(f'  ! {path}: {err}')
        # REL-01/REL-03, D-08: printed unconditionally on every --apply run
        # (never behind --debug) -- how many chunks the 401 verify-then-retry
        # path recovered and how many duplicate uploads it prevented is the
        # single most useful signal for judging whether the 401 problem is
        # actually fixed, including the (common, reassuring) all-zero case.
        print(f'Retries: {result.chunks_retried} chunk(s) retried after a 401, '
              f'{result.items_already_landed} item(s) already landed (duplicate uploads prevented)')
        print(f'{verb_past}: {result.delete_succeeded} succeeded, {len(result.delete_failures)} failed')
        for asset_id, err in result.delete_failures:
            print(f'  ! {asset_id}: {err}')
        print(f'Re-shown: {result.reshow_succeeded} succeeded, {len(result.reshow_failures)} failed')
        for asset_id, err in result.reshow_failures:
            print(f'  ! {asset_id}: {err}')

        return 1 if (result.upload_failures or result.delete_failures
                     or result.reshow_failures) else 0
    except RateLimitError as e:
        # The API is throttling/locking out this account mid-apply (HTTP
        # 429/475). execute_plan aborted the batch rather than emitting N
        # confusing per-item 401s -- surface the single back-off message.
        print(f'Aborted: {e}')
        return 1
    except ConsecutiveWriteFailureError as e:
        # A RUN of consecutive write failures with no 429/475 signal -- the
        # plain-HTTP-401 form of the anti-abuse trip (or another systemic
        # cut-off). execute_plan aborted after the run rather than emitting N
        # confusing per-item errors. Report what succeeded first, then the
        # distinct back-off message (deliberately worded differently from the
        # RateLimitError "Aborted:" path above so the two are distinguishable).
        print(f'{e.result.upload_succeeded} uploads and {e.result.delete_succeeded} '
              f'deletes succeeded before the run of failures.')
        print(str(e))
        return 1
    except GeoMismatchError as e:
        # Root-cause mitigation for the VPN-geo-mismatch write-lockout
        # (Phase 09) -- execute_plan's geo pre-flight aborted before any
        # write happened.
        print(f'VPN/exit IP in {e.found}, account expects {e.expected} — switch your VPN and retry.')
        return 1
    except BudgetExhausted as e:
        # The proactive client-side write budget ran dry and either
        # --no-wait was passed or the computed wait exceeded --max-wait
        # (Phase 09) -- surface how long a retry would need to wait.
        minutes = e.wait_seconds / 60
        print(f'Write budget exhausted, come back in ~{minutes:.0f} min (or pass --no-wait / raise --max-wait).')
        return 1
    except Exception as e:
        # WR-01 fail-loud (D-05): surface post-login API drift instead of a
        # raw traceback.
        print(f'Failed to sync frame: {e}')
        return 1


class _Tee:
    """stdout mirror for --report: the operator keeps the live output while
    the report captures the exact same stream."""

    def __init__(self, stream, buf):
        self._stream, self._buf = stream, buf

    def write(self, s):
        self._stream.write(s)
        self._buf.write(s)
        return len(s)

    def flush(self):
        self._stream.flush()


def _deliver_report(level, report_to, report_tag, *, command, rc, output,
                    started_ts, duration_s) -> None:
    """Send the run report; a delivery problem is a stderr warning, never a
    run failure (the scheduled job's exit code reflects the SYNC, not the
    email)."""
    from pushframe import report as report_mod
    err = report_mod.deliver(level, command=command, rc=rc, output=output,
                             duration_s=duration_s, started_ts=started_ts,
                             tag=report_tag, to=report_to)
    if err:
        print(f'report: not sent — {err}', file=sys.stderr)


def _docs_hint() -> str:
    # The wheel ships no docs (they live on GitHub), so terminal users get
    # URLs, not local paths. Printed ONCE, on failure only — never after a
    # dry run or an aborted confirmation (exit 0).
    return ('Docs — every error message explained: '
            'https://github.com/coredmp95/pushframe/blob/master/docs/ERRORS.md\n'
            '      full command reference: '
            'https://github.com/coredmp95/pushframe/blob/master/docs/CLI.md')


def main(argv=None) -> int:
    rc = _main(argv)
    if rc != 0:
        print(_docs_hint())
    return rc


_SCHEDULE_ADD_FLAGS = ('--pair', '--album', '--frame', '--sync-dir',
                       '--every', '--at', '--batch-size', '--report',
                       '--report-to')

_SCHEDULE_ADD_USAGE = ('usage: pushframe schedule add <job> --pair NAME '
                       '--every Nmin|Nh|Nd [--report DEBUG|INFO|ERROR]')


def _parse_flag_value_tail(
        tail: list[str], *, prefix: str, flags: tuple[str, ...], usage: str,
        next_cmd: str, next_note: str, name_label: str | None = 'job',
        required: tuple[str, ...] = (), required_note: str = '',
        int_flags: tuple[str, ...] = (),
        bool_flags: tuple[str, ...] = (),
) -> tuple[int, str | None, dict | None]:
    """The ONE strict engine for `<name> --flag VALUE …` tails — shared by
    `schedule add` and `config pair add` (factored 2026-10-02; both used to
    hand-roll the same indexer, and both could crash on a malformed tail)
    and by the nameless `schedule report` configurator.

    A named usage error (exit 2, nothing installed) on: a missing name
    (it must come right after the verb, before any --flag), a stray word,
    an unknown flag (valid ones listed), a duplicated flag, a dangling
    flag, a non-integer int_flags value, or a missing `required` flag.
    Every error prints the usage line and the next-step pointer.
    Returns (rc, name, opts): opts values are str, with int_flags cast;
    `bool_flags` land as True and take no value (a bool flag followed by
    another flag is two flags, never a value — so `--to --test` is still
    a dangling `--to`, not a recipient called "--test").

    `name_label=None` parses a NAMELESS tail (`--flag VALUE …` from index
    0) for configurators with no positional; every other contract holds
    unchanged.
    """

    def _fail(msg: str) -> tuple[int, None, None]:
        print(f'{prefix}: {msg}')
        print(usage)
        print(f'run `{next_cmd}` {next_note}')
        return 2, None, None

    if name_label is None:
        name = None
        i = 0
    else:
        if len(tail) < 2 or tail[1].startswith('--'):
            return _fail(f'the {name_label} name is missing — it must come '
                         'right after `add`, before any --flag')
        name = tail[1]
        i = 2
    opts: dict = {}
    while i < len(tail):
        tok = tail[i]
        if not tok.startswith('--'):
            return _fail(f'unexpected "{tok}" — every option is a '
                         '--flag VALUE pair')
        if tok not in flags:
            return _fail(f'unknown option "{tok}" — known ones: '
                         + ', '.join(flags))
        if tok in opts:
            return _fail(f'{tok} given twice — one value per flag')
        if tok in bool_flags:
            opts[tok] = True
            i += 1
            continue
        if i + 1 >= len(tail) or tail[i + 1].startswith('--'):
            return _fail(f'{tok} needs a value — a flag left dangling '
                         'breaks the command')
        opts[tok] = tail[i + 1]
        i += 2
    for flag in int_flags:
        if flag not in opts:
            continue
        try:
            opts[flag] = int(opts[flag])
        except ValueError:
            return _fail(f'{flag} must be a number, got "{opts[flag]}"')
    for flag in required:
        if flag not in opts:
            return _fail(f'{flag} is missing — {required_note}')
    return 0, name, opts


def _parse_schedule_add(sub: list[str]) -> tuple[int, dict | None]:
    """The `schedule add` spelling of the shared tail parser — a named
    usage error (exit 2) on any malformed tail, never a crash (the venus
    2026-10-02 IndexError regression: a dangling flag used to die raw, and
    a flag right after `add` silently became the job name)."""
    rc, job, opts = _parse_flag_value_tail(
        sub, prefix='schedule add', flags=_SCHEDULE_ADD_FLAGS,
        usage=_SCHEDULE_ADD_USAGE, next_cmd='pushframe schedule --help',
        next_note='for the full map (what gets scheduled, email reports, '
                  'examples)',
        name_label='job', int_flags=('--batch-size',))
    if rc != 0:
        return rc, None
    return 0, {'job': job, 'pair': opts.get('--pair'),
               'album': opts.get('--album'), 'frame': opts.get('--frame'),
               'sync_dir': opts.get('--sync-dir'),
               'every': opts.get('--every'), 'at': opts.get('--at'),
               'batch_size': opts.get('--batch-size'),
               'report': opts.get('--report'),
               'report_to': opts.get('--report-to')}


_PAIR_ADD_USAGE = ('usage: pushframe config pair add <name> --album ALBUM '
                   '--frame FRAME')


def _parse_pair_add(sub: list[str]) -> tuple[int, str | None, str | None,
                                             str | None]:
    """The `config pair add` spelling of the shared tail parser — a named
    usage error (exit 2) on any malformed tail, never a crash and never a
    pair literally named `--album` (audit 2026-10-02: the loose `sub[1]`
    + `sub.index(flag)+1` pair did both)."""
    rc, name, opts = _parse_flag_value_tail(
        sub, prefix='pair add', flags=('--album', '--frame'),
        usage=_PAIR_ADD_USAGE, next_cmd='pushframe config pair --help',
        next_note='for what a pair is and how to use it',
        name_label='pair', required=('--album', '--frame'),
        required_note='a pair maps an album to a frame, both are required')
    if rc != 0:
        return rc, None, None, None
    return 0, name, opts['--album'], opts['--frame']


def _scheduled_run_header(report_tag: str | None) -> None:
    """First line a scheduled run writes to its job log — the run's identity
    (which night did what must never require journalctl). Only the scheduled
    paths print it: it exists for the append-only job logs."""
    from datetime import datetime
    from pushframe import __version__
    stamp = datetime.now().astimezone().isoformat(timespec='seconds')
    print(f'=== run {stamp} job={report_tag or "-"} pushframe={__version__} ===')


def _scheduled_run_footer(rc: int, elapsed_s: float) -> None:
    """Closes the run's log block. An exit by exception has no footer — the
    traceback and systemd's Failed line carry that story."""
    print(f'=== run end rc={rc} elapsed={round(elapsed_s)}s ===')


JOB_LOG_KEEP_GENERATIONS = 28


def _job_log_keep() -> int:
    """Ops knob for the rotation history depth: PUSHFRAME_JOB_LOG_KEEP.
    A junk or <1 value falls back to the default — a retention typo must
    never fail a run that already succeeded (this function's only contract).
    """
    try:
        keep = int(os.getenv('PUSHFRAME_JOB_LOG_KEEP', ''))
    except ValueError:
        return JOB_LOG_KEEP_GENERATIONS
    return keep if keep >= 1 else JOB_LOG_KEEP_GENERATIONS


def _rotate_job_log_if_large(report_tag: str | None, *,
                             limit_bytes: int = 5 * 1024 * 1024,
                             keep: int | None = None) -> bool:
    """Bound a job's append log while keeping history consultable (systemd
    reopens the file with O_APPEND every start): past `limit_bytes`, the log
    slides one generation older — <job>.log.1 … <job>.log.<keep>, the oldest
    generation falling off the end — so the live file stays bounded AND the
    previous weeks remain readable. Generations are size-bounded, not
    time-bounded: a nightly job crossing the limit every night keeps ~4
    weeks (default 28); a quiet one keeps months. Worst case per job:
    keep × limit bytes. Called at run END: this process's still-open stdout
    fd keeps appending to the renamed inode until exit, and the next start
    opens a fresh <job>.log, so nothing is lost and no fd surgery is needed.
    A rotation failure must never fail a run that already succeeded.
    """
    if not report_tag:
        return False
    if keep is None:
        keep = _job_log_keep()
    if keep < 1:
        raise ValueError(f'keep={keep!r} must be >= 1')
    from pushframe.schedule import LOG_DIR
    log = LOG_DIR / f'{report_tag}.log'
    try:
        if not log.exists() or log.stat().st_size <= limit_bytes:
            return False
        # Slide every existing generation one slot older, oldest falling off
        # (os.replace overwrites the destination): .log.(keep-1) -> .log.keep,
        # ..., .log.1 -> .log.2, then the live log -> .log.1.
        for slot in range(keep - 1, 0, -1):
            src = log.parent / f'{log.name}.{slot}'
            if src.exists():
                os.replace(src, log.parent / f'{log.name}.{slot + 1}')
        os.replace(log, log.parent / (log.name + '.1'))
        # Generations beyond `keep` can only exist after an operator LOWERED
        # the retention (env or code); leftovers would linger as unbounded
        # silent tails, so sweep them — tolerantly, same run-success rule.
        try:
            prefix = log.name + '.'
            for stale in log.parent.iterdir():
                if not stale.name.startswith(prefix):
                    continue
                suffix = stale.name[len(prefix):]
                if suffix.isdigit() and int(suffix) > keep:
                    stale.unlink()
        except OSError:
            pass
        return True
    except OSError:
        return False


def _main(argv=None) -> int:
    load_dotenv()
    # IDN-03 (phase 20): first run on an existing auraframes install migrates
    # the config home to ~/.config/pushframe/ — one notice line, idempotent,
    # never destructive. Runs before any command touches the config.
    from pushframe.migration import migration_notice
    _notice = migration_notice()
    if _notice:
        print(_notice)
    parser = build_parser()
    # `config` sub-args carry their own --flags (pair add --album …); split
    # them out BEFORE argparse so they are not rejected as unknown options.
    # `schedule` gets the same treatment (its `add --report … --report-to …`
    # and `report --to … --smtp-host …` flags are hand-parsed downstream).
    argv_list = list(argv) if argv is not None else None
    config_tail = []
    schedule_tail = []
    # The verb word is matched at argv POSITION ONE only (`pushframe
    # config …`): matching it anywhere used to truncate the command line
    # at a literal frame/job name — `google-sync "Cadre" --frame config`
    # (a frame named "config") left argparse a dangling --frame.
    verb = (argv_list[0] if argv_list is not None
            else (sys.argv[1] if len(sys.argv) > 1 else None))
    if verb == 'config':
        if argv_list is not None:
            config_tail = argv_list[1:]
            argv_list = argv_list[:1]
        else:
            config_tail = sys.argv[2:]
            sys.argv = sys.argv[:2]
    elif verb == 'schedule':
        schedule_tail = list(argv_list[1:] if argv_list is not None
                             else sys.argv[2:])
        if argv_list is not None:
            argv_list = argv_list[:1]
        else:
            sys.argv = sys.argv[:2]
        if schedule_tail and schedule_tail[0] in ('-h', '--help'):
            # argparse owns bare --help (the epilog map): re-attach it and
            # let the normal path print it (the 5.1.13 stripping swallowed
            # `schedule --help` into a bare usage line). The word `help`
            # stays in the tail — the dispatcher prints the map for it.
            if argv_list is not None:
                argv_list = argv_list + [schedule_tail[0]]
            else:
                sys.argv = sys.argv + [schedule_tail[0]]
            schedule_tail = []
    args = parser.parse_args(argv_list)
    if args.command == 'config' and config_tail:
        args.config_args = config_tail
    if args.command == 'schedule':
        args.schedule_args = schedule_tail
        if schedule_tail and schedule_tail[0] == 'help':
            # The word (bare `schedule help`) prints the same full map as
            # `schedule --help` — the tail stripping means argparse never
            # saw it.
            parser.parse_args(['schedule', '--help'])

    if args.command == 'config':
        return run_config(wizard_args=getattr(args, 'config_args', []) or [])
    if args.command == 'doctor':
        from pushframe.doctor import run_doctor
        return run_doctor(args.frame, do_write=not args.no_write,
                          debug=args.debug)
    if args.command == 'schedule':
        from pushframe import schedule as sch
        sub = list(args.schedule_args or [])
        if not sub or sub[0] == 'list':
            if len(sub) > 1:
                print(f'schedule list: unexpected "{sub[1]}" — '
                      '`pushframe schedule list` takes no arguments')
                return 2
            return sch.schedule_list()
        if sub[0] == 'report':
            from pushframe import report as report_mod
            return report_mod.configure(sub[1:])
        if sub[0] == 'add':
            if len(sub) > 1 and sub[1] in ('-h', '--help'):
                parser.parse_args(['schedule', '--help'])
            rc, add = _parse_schedule_add(sub)
            if rc != 0:
                return rc
            return sch.schedule_add(**add)
        if sub[0] == 'remove' and len(sub) >= 2:
            if sub[1].startswith('--'):
                print('schedule remove: needs a job name — '
                      '`pushframe schedule list` shows the installed ones')
                return 2
            if len(sub) > 2:
                print(f'schedule remove: unexpected "{sub[2]}" — '
                      'usage: pushframe schedule remove <job>')
                return 2
            return sch.schedule_remove(sub[1])
        if sub[0] == 'help':
            # Caught upstream too; this branch keeps the contract local.
            parser.parse_args(['schedule', '--help'])
        print('usage: pushframe schedule add <job> … | schedule list | '
              'schedule remove <job>')
        print('run `pushframe schedule --help` for the full map '
              '(what gets scheduled, email reports, examples)')
        return 1
    if args.command == 'logout':
        return run_logout()
    if args.command == 'status':
        return run_status(debug=args.debug)
    if args.command == 'google-link':
        return run_google_link(debug=args.debug)
    if args.command == 'google-album':
        return run_google_album(args.target, list_all=args.list,
                                verbose=args.verbose, debug=args.debug)
    if args.command == 'google-sync':
        from pushframe.gsync import run_google_sync
        if not args.all_pairs and not args.pair and not args.frame:
            print('google-sync: give --frame FRAME (one album→frame), '
                  '--pair NAME (a named pair), or --all (every configured '
                  'pair)')
            return 2
        if (args.pair or args.all_pairs) and (args.album or args.frame):
            print('note: with --pair/--all the album and frame come from the '
                  'config — the positional album and --frame are ignored')
        target = (f'--pair {args.pair}' if args.pair
                  else '--all' if args.all_pairs
                  else f'{args.album!r} --frame {args.frame!r}')
        command = f'google-sync {target}'
        import time as _time
        if args.scheduled:
            _scheduled_run_header(args.report_tag)
        _t0 = _time.monotonic()
        if args.report:
            # --report mirrors stdout (the operator keeps the live output)
            # and emails the captured run per the level's contract.
            import contextlib
            import io as _io
            started = _time.time()
            buf = _io.StringIO()
            with contextlib.redirect_stdout(_Tee(sys.stdout, buf)):
                rc = run_google_sync(args.album, args.frame or '--all',
                                     apply=args.apply, yes=args.yes,
                                     debug=args.debug,
                                     batch_size=args.batch_size,
                                     pair=args.pair,
                                     run_all=args.all_pairs,
                                     scheduled=args.scheduled)
            _deliver_report(args.report, args.report_to, args.report_tag,
                            command=command, rc=rc, output=buf.getvalue(),
                            started_ts=started,
                            duration_s=_time.time() - started)
        else:
            rc = run_google_sync(args.album, args.frame or '--all',
                                 apply=args.apply, yes=args.yes,
                                 debug=args.debug, batch_size=args.batch_size,
                                 pair=args.pair,
                                 run_all=args.all_pairs,
                                 scheduled=args.scheduled)
        if args.scheduled:
            # The job log carries the run's own bookkeeping — identity
            # (header above), end (rc + elapsed), then the size bound.
            _scheduled_run_footer(rc, _time.monotonic() - _t0)
            _rotate_job_log_if_large(args.report_tag)
        return rc
    if args.command == 'inspect':
        return run_inspect(args.frame, debug=args.debug)
    if args.command == 'sync':
        removal_mode = 'hard_delete' if args.hard_delete else ('delete' if args.delete else 'hide')
        import time as _time
        if args.scheduled:
            _scheduled_run_header(args.report_tag)
        _t0 = _time.monotonic()
        rc = run_sync(args.dir, args.frame, apply=args.apply, yes=args.yes, debug=args.debug,
                      removal_mode=removal_mode)
        if args.scheduled:
            # Same bookkeeping contract as scheduled google-sync: the job
            # log carries the run's identity (header above), end (rc +
            # elapsed), then the size bound — <job>.log alone answers which
            # night synced what.
            _scheduled_run_footer(rc, _time.monotonic() - _t0)
            _rotate_job_log_if_large(args.report_tag)
        return rc
    if args.command == 'push':
        return run_sync(
            args.dir, args.frame, apply=args.apply, yes=args.yes, debug=args.debug,
            no_delete=True, limit=args.limit, batch_size=args.batch_size,
            chunk_delay=args.chunk_delay, verb='push',
            max_wait=args.max_wait, no_wait=args.no_wait,
            country=args.country, ignore_budget=args.ignore_budget,
        )
    if args.command == 'reconcile':
        return run_reconcile(
            args.frame, remove=args.remove, yes=args.yes, mechanism=args.mechanism,
            max_age_hours=args.max_age_hours, include_unknown_age=args.include_unknown_age,
            debug=args.debug,
        )
    raise ValueError(f'Unhandled command: {args.command}')


if __name__ == '__main__':
    sys.exit(main())
