"""Early warning for SILENT Google Photos wire-format changes.

The 2026-10-13/14 incident is the model: Google moved the snAcKc continuation
cursor from `AH_...` to `AKFE...` at `inner[2]`. The SAFE-01 gates kept the
mirror safe, but the failure said "transient" and diagnosing it took live
probes. This module makes the next such change obvious from the nightly
report alone:

- a privacy-safe STRUCTURAL FINGERPRINT of every run's Google answers (built
  in `parsers.py` / `enumerate.py`; this module stores, compares and renders
  it);
- a per-target "last good run" store, so a change prints ONE
  `WARNING: Google response format changed: ...` line, even when the run
  succeeds (the report layer escalates it);
- (later in this module) failure diagnostics and a consecutive-failure
  streak.

Privacy posture: a fingerprint is made of value kinds, power-of-two length
buckets and at most the first 4 characters of a cursor. It never contains an
id, a URL, a title or a full cursor, so it is safe to e-mail and to paste in
a public issue.

State (one small JSON file per sync target, mode 0600 in 0700 directories,
written atomically):

- `~/.local/state/pushframe/pairs/<pair>/google-wire.json` for `--pair` /
  `--all` runs;
- `~/.local/state/pushframe/google-wire/album-<first 16 hex of
  sha1(album target)>.json` otherwise (the raw target never appears in a
  path).

```json
{"version": 1,
 "fingerprint": {"inner": "...", "cursor": "...", "item": "...", "albums": "..."},
 "failure_class": null, "consecutive_failures": 0,
 "updated": "2026-10-14T21:00:00+00:00"}
```

Tolerance rules: the watch is advisory. A missing, unreadable, malformed or
unwritable state file never crashes or alters a sync — it is treated as
absent and reported with one `note:` line. A field is compared only when both
the stored and the current value are present, and the stored value is merged
field by field (a short single page legitimately has no cursor).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from pushframe.google.redaction import redact_tokens
from pushframe.utils.io import ensure_private_dir
from pushframe.utils.names import safe_name_problem

FINGERPRINT_FIELDS = ("inner", "cursor", "item", "albums")
FORMAT_CHANGE_MARKER = "WARNING: Google response format changed"
DIAGNOSTICS_HEADER = ("Diagnostics (Google wire shape only — no ids, links "
                      "or titles):")
ESCALATION_MARKER = ("probable Google protocol change — pushframe likely "
                     "needs an update")
RELEASES_URL = "https://github.com/coredmp95/pushframe/releases"

STATE_VERSION = 1
_MAX_VALUE_LEN = 200
_VALUE_RE = re.compile(r"[\x20-\x7e]{1,200}")
_CLASS_RE = re.compile(r"[a-z0-9:-]{1,64}")
_STATE_KEYS = frozenset(
    {"version", "fingerprint", "failure_class", "consecutive_failures", "updated"})

# Patched by tests (tests/conftest.py points it at a fresh tmp dir per test so
# no test ever touches the real ~/.local/state).
STATE_ROOT_OVERRIDE: Path | None = None


# --- paths --------------------------------------------------------------------

def state_root() -> Path:
    """The pushframe state dir, resolved at call time (HOME honoured) and
    expanded on the same line (a literal `~` Path would be relative)."""
    if STATE_ROOT_OVERRIDE is not None:
        return Path(STATE_ROOT_OVERRIDE)
    return Path("~/.local/state/pushframe").expanduser()


def state_path_for(*, pair: str | None = None,
                   album_target: str | None = None) -> Path | None:
    """State file for a sync target; None disables the watch for it."""
    if pair is not None:
        if safe_name_problem(pair, what="pair name"):
            return None
        return state_root() / "pairs" / pair / "google-wire.json"
    if not album_target or not str(album_target).strip():
        return None
    # sha1 is a stable non-secret file-name key here (plan-mandated), not a
    # security primitive.
    digest = hashlib.sha1(str(album_target).strip().encode("utf-8"),
                          usedforsecurity=False).hexdigest()[:16]
    return state_root() / "google-wire" / f"album-{digest}.json"


# --- pure fingerprint helpers -------------------------------------------------

def render_fingerprint(fp: dict | None) -> str:
    """`inner=... cursor=... item=... albums=...` (fixed order, absent fields
    skipped) or `(none)`."""
    parts = [f"{name}={fp[name]}" for name in FINGERPRINT_FIELDS
             if fp and fp.get(name)]
    return " ".join(parts) if parts else "(none)"


def fingerprint_diff(old: dict | None, new: dict | None) -> list[tuple[str, str, str]]:
    """(field, old, new) for every field present in BOTH and different."""
    out = []
    for name in FINGERPRINT_FIELDS:
        a = (old or {}).get(name)
        b = (new or {}).get(name)
        if a and b and a != b:
            out.append((name, a, b))
    return out


def merge_fingerprint(old: dict | None, new: dict | None) -> dict:
    """Field-by-field merge: the new value wins, the old one is kept where the
    new one is absent."""
    merged = {}
    for name in FINGERPRINT_FIELDS:
        value = (new or {}).get(name) or (old or {}).get(name)
        if value:
            merged[name] = value
    return merged


def _clean(fp: dict | None) -> dict:
    """Only known fields with str values; over-long values are truncated so a
    fingerprint can always be stored under the closed state schema."""
    out = {}
    for name in FINGERPRINT_FIELDS:
        value = (fp or {}).get(name)
        if not isinstance(value, str) or not value:
            continue
        value = "".join(c if " " <= c <= "~" else "?" for c in value)
        if len(value) > _MAX_VALUE_LEN:
            value = value[:_MAX_VALUE_LEN - 3] + "..."
        out[name] = value
    return out


def failure_class(exc) -> str | None:
    """The streak class of a Google-side failure, None when it is not a wire
    failure (frame-side / manifest errors are never counted). Duck-typed on
    `kind` so this module never imports gsync."""
    from pushframe.google.enumerate import EnumerateError

    if isinstance(exc, EnumerateError):
        return f"enumerate:{exc.kind}"
    kind = getattr(exc, "kind", None)
    if isinstance(kind, str) and kind.startswith("listing:"):
        return kind
    return None


# --- state file ---------------------------------------------------------------

def _fresh_state() -> dict:
    return {"version": STATE_VERSION, "fingerprint": {}, "failure_class": None,
            "consecutive_failures": 0, "updated": ""}


def _schema_problem(data) -> str | None:
    """None when `data` satisfies the closed state schema, else a short reason
    (never file content)."""
    if not isinstance(data, dict):
        return "not a JSON object"
    if not set(data) <= _STATE_KEYS:
        return "unknown keys"
    if data.get("version") != STATE_VERSION:
        return "unsupported version"
    fp = data.get("fingerprint", {})
    if not isinstance(fp, dict) or not set(fp) <= set(FINGERPRINT_FIELDS):
        return "bad fingerprint"
    if not all(isinstance(v, str) and _VALUE_RE.fullmatch(v) for v in fp.values()):
        return "bad fingerprint value"
    fc = data.get("failure_class")
    if fc is not None and not (isinstance(fc, str) and _CLASS_RE.fullmatch(fc)):
        return "bad failure_class"
    count = data.get("consecutive_failures", 0)
    if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 10**6:
        return "bad consecutive_failures"
    if not isinstance(data.get("updated", ""), str):
        return "bad updated"
    return None


class WireWatch:
    """Per-target wire-format state. `path=None` disables every method.

    Every load and save is tolerant: problems become one queued `note:` line
    and never an exception (the watch must not alter a sync)."""

    def __init__(self, path) -> None:
        self._path = Path(path) if path is not None else None
        self._state: dict | None = None
        self._notes: list[str] = []

    # -- persistence --
    def _load(self) -> dict:
        if self._state is not None:
            return self._state
        state = _fresh_state()
        reason = None
        try:
            raw = self._path.read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            raw = None
        except OSError as exc:
            raw, reason = None, type(exc).__name__
        if raw is not None:
            try:
                data = json.loads(raw.decode("utf-8"))
            except ValueError as exc:  # UnicodeDecodeError / JSONDecodeError
                reason = type(exc).__name__
            else:
                reason = _schema_problem(data)
                if reason is None:
                    state.update(data)
                    state["fingerprint"] = dict(data.get("fingerprint", {}))
        if reason is not None:
            self._notes.append(
                f"note: Google wire-format state {self._path} was unreadable "
                f"({reason}) — starting fresh; the sync itself is unaffected")
        self._state = state
        return state

    def _save(self, state: dict) -> None:
        state["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        tmp = None
        try:
            ensure_private_dir(self._path.parent)
            payload = json.dumps(state, indent=2, sort_keys=True).encode("utf-8")
            tmp = self._path.with_name(self._path.name + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, payload)
            finally:
                os.close(fd)
            os.replace(tmp, self._path)
            os.chmod(self._path, 0o600)
        except OSError as exc:
            self._notes.append(
                f"note: could not save Google wire-format state {self._path} "
                f"({type(exc).__name__}) — the sync itself is unaffected")
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def _take_notes(self) -> list[str]:
        notes, self._notes = self._notes, []
        return notes

    # -- API --
    def last_good(self) -> dict | None:
        """The stored fingerprint of the last run whose listing passed every
        SAFE-01 gate; None when disabled or nothing is stored."""
        if self._path is None:
            return None
        return dict(self._load()["fingerprint"]) or None

    def on_failure(self, failure_cls: str | None,
                   fingerprint: dict | None) -> list[str]:
        """A Google-side failure of class `failure_cls`: bump the consecutive
        streak (a different class restarts it) and return the lines to print.
        The stored fingerprint is NEVER touched — last good stays last good.
        A None class (not a wire failure) is a no-op."""
        if self._path is None or failure_cls is None:
            return []
        state = self._load()
        if state["failure_class"] == failure_cls:
            state["consecutive_failures"] += 1
        else:
            state["failure_class"] = failure_cls
            state["consecutive_failures"] = 1
        count = state["consecutive_failures"]
        self._save(state)
        lines = self._take_notes()
        if count == 1:
            lines.append(
                f'note: first "{failure_cls}" failure in a row for this sync '
                f"target — may be a transient Google answer; nothing on the "
                f"frame was changed and the next run retries (a second "
                f'"{failure_cls}" failure in a row is escalated as an '
                f"ATTENTION).")
        else:
            lines.append(
                f'ATTENTION: {ESCALATION_MARKER}: "{failure_cls}" failed '
                f"{count} runs in a row for this sync target — check "
                f"{RELEASES_URL} for a newer pushframe; if there is none "
                f"yet, open an issue with this run's output (it carries no "
                f"ids, links or titles).")
            diff = fingerprint_diff(state["fingerprint"], _clean(fingerprint))
            if diff:
                changes = "; ".join(f"{n} {a} → {b}" for n, a, b in diff)
                lines.append(
                    f"  wire fingerprint changed since the last good run: "
                    f"{changes}")
        return lines

    def on_listing_ok(self, fingerprint: dict | None) -> list[str]:
        """The listing passed every SAFE-01 gate: compare, store, return the
        lines to print (queued notes first, then at most one WARNING)."""
        if self._path is None:
            return []
        state = self._load()
        new = _clean(fingerprint)
        old = state["fingerprint"]
        lines: list[str] = []
        diff = fingerprint_diff(old, new) if old else []
        if diff:
            changes = "; ".join(f"{name} {a} → {b}" for name, a, b in diff)
            lines.append(
                f"{FORMAT_CHANGE_MARKER}: {changes} — the listing still "
                f"passed every safety gate; check for a pushframe update: "
                f"{RELEASES_URL}")
        state["fingerprint"] = merge_fingerprint(old, new)
        state["failure_class"] = None
        state["consecutive_failures"] = 0
        self._save(state)
        return self._take_notes() + lines


# --- diagnostics --------------------------------------------------------------

_PAGES_HEAD, _PAGES_TAIL, _PAGES_MAX = 6, 5, 12


def format_diagnostics(*, pages, fingerprint, last_good=None,
                       metadata_count: int | None = None,
                       listed: int | None = None,
                       show_last_good: bool = True) -> list[str]:
    """Compact, user-data-free block printed after a Google listing failure.

    One line per snAcKc answer (item count, cursor shape, note), the run's
    fingerprint, the last good one and what changed, and the album metadata
    count against the listed count with the shortfall tolerance. Pure: no
    state access. Every line goes through `redact_tokens`."""
    from pushframe.google.enumerate import listing_shortfall_tolerance

    lines = [DIAGNOSTICS_HEADER]

    def _page_line(page) -> str:
        line = f"  page {page.post}: {page.items} item(s), cursor {page.cursor}"
        return f"{line} — {page.note}" if page.note else line

    pages = list(pages or [])
    if not pages:
        lines.append("  pages: none fetched")
    elif len(pages) > _PAGES_MAX:
        lines += [_page_line(p) for p in pages[:_PAGES_HEAD]]
        lines.append(f"  … {len(pages) - _PAGES_HEAD - _PAGES_TAIL} more page(s)")
        lines += [_page_line(p) for p in pages[-_PAGES_TAIL:]]
    else:
        lines += [_page_line(p) for p in pages]

    lines.append(f"  fingerprint:   {render_fingerprint(fingerprint)}")
    if show_last_good:
        if last_good:
            lines.append(f"  last good run: {render_fingerprint(last_good)}")
            diff = fingerprint_diff(last_good, fingerprint)
            if diff:
                changes = "; ".join(f"{n} {a} → {b}" for n, a, b in diff)
                lines.append(f"  changed since the last good run: {changes}")
            elif fingerprint:
                lines.append("  unchanged since the last good run")
        else:
            lines.append("  last good run: (none stored)")
    if metadata_count is not None:
        lines.append(
            f"  album metadata: {metadata_count} item(s), listed: "
            f"{'?' if listed is None else listed} (shortfall tolerance "
            f"{listing_shortfall_tolerance(metadata_count)})")
    elif listed is not None:
        lines.append(f"  listed: {listed}")
    return [redact_tokens(line) for line in lines]
