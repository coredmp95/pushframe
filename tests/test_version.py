"""Phase 22 (REL-02): the version story.

One source of truth: `pushframe.__version__`. It must match pyproject's
`project.version` (the release workflow re-asserts both against the git
tag), and `pushframe --version` must print `pushframe <version>` and exit
0 — the first distribution release is 5.0.0 (phase 22 decision D-03);
0.1.0 must never reach PyPI.
"""
import tomllib
from pathlib import Path

import pytest

from pushframe import __version__

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_version_is_first_distribution_release():
    """The current distribution release (moved off 0.1.0 at 5.0.0; page
    metadata fix shipped in 5.0.4)."""
    assert __version__ == "5.1.26"


def test_version_matches_pyproject_single_source():
    """The __init__ constant and pyproject stay in lockstep (D-08 lineage)."""
    data = tomllib.loads(PYPROJECT.read_text())
    assert data["project"]["version"] == __version__


def test_cli_version_flag_prints_and_exits_zero(capsys):
    """`pushframe --version` prints `pushframe <version>` and exits 0."""
    from pushframe.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.strip() == f"pushframe {__version__}"
