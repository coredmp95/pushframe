"""Build hook: make the README's relative doc links absolute for PyPI.

The README doubles as PyPI's project page (long_description). PyPI renders
it with NO repository context, so links like `docs/CLI.md` resolve against
pypi.org — dead ends (2026-10-02 report: the project-page doc links were
broken). Hatchling reads `readme = "README.md"` straight from disk when it
first touches metadata — BEFORE the build hooks run (validate_fields()
touches every field and caches `metadata.core._readme`) — so this hook:

1. rewrites README.md in place at initialize(),
2. patches the already-cached `metadata.core._readme`,
3. registers an atexit handler that restores the original file — so the
   checkout is repaired no matter how the build ends (success, error,
   or Ctrl-C), without relying on finalize() ordering.

Idempotent by construction: the regex only matches link targets NOT
already starting with a scheme, a slash, or an in-page anchor (#), so
re-running never double-prefixes. A crash still leaves the backup
`.README-orig.md`; the next build restores from it before rewriting.
"""
from __future__ import annotations

import atexit
import re
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_REPO = "https://github.com/coredmp95/pushframe/blob/master"
_ORIG = ".README-orig.md"
# A markdown link target that is relative: not a URL (scheme:), not
# site-absolute (/), not an in-page anchor (#), not a mailto. Anchors may
# follow a relative path (docs/CLI.md#known-issues) and are preserved.
_RELATIVE_LINK = re.compile(
    r"\]\((?!https?://|/|#|mailto:)([^)#]+)(#[^)]*)?\)")


class BuildHook(BuildHookInterface):
    def _restore(self, *_args) -> None:
        readme = Path(self.root) / "README.md"
        backup = Path(self.root) / _ORIG
        if backup.is_file():
            readme.write_text(backup.read_text(encoding="utf-8"),
                              encoding="utf-8")
            backup.unlink()

    def initialize(self, version, build_data):
        readme = Path(self.root) / "README.md"
        if not readme.is_file():
            return
        text = readme.read_text(encoding="utf-8")
        backup = Path(self.root) / _ORIG
        if backup.is_file():
            # A previous build crashed before its restore ran: start from
            # the original so we never double-prefix.
            text = backup.read_text(encoding="utf-8")
            readme.write_text(text, encoding="utf-8")
        rewritten = _RELATIVE_LINK.sub(
            lambda m: f"]({_REPO}/{m.group(1)}{m.group(2) or ''})", text)
        if rewritten == text:
            return  # nothing relative to rewrite — leave the repo alone
        backup.write_text(text, encoding="utf-8")
        atexit.register(self._restore)
        readme.write_text(rewritten, encoding="utf-8")
        # Hatchling caches metadata.core._readme at its FIRST access —
        # validate_fields() touches every field before the build hooks run.
        # Patch the cached value so the sdist's PKG-INFO and the wheel's
        # METADATA both carry the absolute links.
        self.metadata.core._readme = rewritten
