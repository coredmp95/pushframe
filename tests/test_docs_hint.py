"""The failure-only docs footer: every non-zero exit points the user at
docs/ERRORS.md and docs/CLI.md on GitHub (the wheel ships no docs), and no
successful run — a dry run, an aborted confirmation, a plain listing — ever
sees it. Tested against main()'s wrapper seam, not the runners.
"""
import pytest

from pushframe import cli


def test_docs_hint_names_errors_and_cli_reference():
    hint = cli._docs_hint()
    assert 'docs/ERRORS.md' in hint
    assert 'docs/CLI.md' in hint
    assert hint.count('https://github.com/coredmp95/pushframe/blob/master/') == 2


def test_main_prints_the_hint_on_failure(monkeypatch, capsys):
    monkeypatch.setattr(cli, '_main', lambda argv=None: 1)
    assert cli.main([]) == 1
    out = capsys.readouterr().out
    assert 'docs/ERRORS.md' in out and 'docs/CLI.md' in out


def test_main_never_prints_the_hint_on_success(monkeypatch, capsys):
    monkeypatch.setattr(cli, '_main', lambda argv=None: 0)
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert 'docs/ERRORS.md' not in out and 'docs/CLI.md' not in out
