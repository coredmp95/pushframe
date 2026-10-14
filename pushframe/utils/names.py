"""The one rule for operator-chosen names that become files or unit names.

A pair name is a directory component (per-pair manifest and cache shards
under ``pairs/<name>/``); a schedule job name is a systemd unit file name
(``pushframe-<job>.service``), a log file name and an argv token
(``--report-tag``). Both therefore obey the same strict rule: 1-64 ASCII
letters, digits, ``.``, ``_`` or ``-``, starting with a letter or digit.
ASCII-only because systemd unit names are ASCII; no leading ``.`` (hidden
files, ``.``/``..``) and no leading ``-`` (would parse as a flag); no
``/`` or control characters. Stdlib only.
"""
import re

_SAFE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')


def safe_name_problem(name, *, what: str) -> str | None:
    """None when `name` is acceptable, else one sentence naming the problem
    and the rule. fullmatch (never match + `$`: `$` accepts a trailing
    newline). repr keeps control characters visible and inert in logs."""
    if isinstance(name, str) and _SAFE_NAME.fullmatch(name):
        return None
    return (f'{what} {name!r} must be 1-64 ASCII letters, digits, ".", "_" '
            f'or "-", starting with a letter or digit — it becomes a file, '
            f'directory and systemd unit name, and a leading "-" would parse '
            f'as a flag (example: cadre-venus)')
