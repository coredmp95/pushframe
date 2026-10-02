"""The hatch_build.py hook: PyPI's project page has no repo context.

The README doubles as PyPI's long_description; relative links like
docs/CLI.md resolve against pypi.org there — dead ends (2026-10-02
report). The hook rewrites them to absolute GitHub URLs at build time and
restores the checkout afterwards (atexit, not finalize ordering).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import the regex constants WITHOUT executing hatch_build.py's module
# body: it imports hatchling, a build-time dependency the test env does
# not carry (deliberately — see docs/DEVELOPING.md's install channels).
_src = (Path(__file__).resolve().parent.parent / "hatch_build.py").read_text(
    encoding="utf-8")
_repo = re.search(r'_REPO = "([^"]+)"', _src).group(1)
_relative_link = re.compile(
    r"\]\((?!https?://|/|#|mailto:)([^)#]+)(#[^)]*)?\)")
assert _repo.endswith("/blob/master")  # the hook file's shape is what we test


def _rewrite(text: str) -> str:
    return _relative_link.sub(
        lambda m: f"]({_repo}/{m.group(1)}{m.group(2) or ''})", text)


def test_relative_links_become_absolute(tmp_path):
    src = tmp_path / "README.md"
    src.write_text(
        "[CLI](docs/CLI.md) [E](docs/ERRORS.md#known-issues) "
        "[C](CHANGELOG.md) [S](SECURITY.md)")
    out = _rewrite(src.read_text())
    assert "](https://github.com/coredmp95/pushframe/blob/master/docs/CLI.md)" in out
    assert "](https://github.com/coredmp95/pushframe/blob/master/docs/ERRORS.md#known-issues)" in out
    assert "](https://github.com/coredmp95/pushframe/blob/master/CHANGELOG.md)" in out
    assert "](https://github.com/coredmp95/pushframe/blob/master/SECURITY.md)" in out


def test_absolute_anchors_and_scheme_links_are_untouched(tmp_path):
    src = tmp_path / "README.md"
    src.write_text(
        "[gh](https://github.com/coredmp95/pushframe) "
        "[anchor](#install) [abs](/rooted) [mail](mailto:x@y.z) "
        "[in text](not-a-link) it stays")
    out = _rewrite(src.read_text())
    assert "](https://github.com/coredmp95/pushframe)" in out   # untouched
    assert "](#install)" in out                                 # untouched
    assert "](/rooted)" in out                                  # untouched
    assert "](mailto:x@y.z)" in out                             # untouched


def test_idempotent_second_pass_changes_nothing(tmp_path):
    src = tmp_path / "README.md"
    src.write_text("[CLI](docs/CLI.md)")
    once = _rewrite(src.read_text())
    assert _rewrite(once) == once  # never double-prefixes


def test_real_readme_has_relative_links_for_the_hook_to_rewrite():
    """The repo README must stay relative (GitHub/IDEs) — that is the
    invariant the hook's rewrite-and-restore relies on; if someone
    absolutizes the README by hand, the hook becomes a no-op and this
    test asks why."""
    readme = Path(__file__).resolve().parent.parent / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert "](docs/" in text
    # the hook's own absolute prefix must never leak into the repo file
    assert "](https://github.com/coredmp95/pushframe/blob/master/" not in text
