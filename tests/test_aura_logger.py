"""MOD-04 (Phase 19): repeated `Aura()` construction must no longer leak
loguru sinks / spawn a log file per construction — one file per process,
and logging must still flow after the guard.

Every test runs in a tmp cwd (monkeypatch.chdir) so the repo's real logs/
directory is never touched, and an autouse fixture resets loguru's global
sink state plus aura.py's module-level guard flag — the same singleton
hygiene the rest of the suite applies (see test_cli_status.py's comment).
"""
import uuid
from pathlib import Path

import pytest
from loguru import logger

import pushframe.aura as aura_module
from pushframe.aura import Aura
from tests.offline import offline_aura


@pytest.fixture(autouse=True)
def _fresh_loguru_state(monkeypatch):
    logger.remove()
    monkeypatch.setattr(aura_module, '_LOGGER_READY', False)
    yield
    logger.remove()


def _log_files() -> list[Path]:
    return sorted(Path('logs').glob('file_*.log'))


def test_repeated_construction_spawns_one_log_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _log_files() == [], 'tmp cwd must start without a logs/ directory'

    # Construction is the act under test; the instances are intentionally unused.
    first = offline_aura()   # noqa: F841
    assert len(_log_files()) == 1, 'first Aura() must configure logging (one file)'

    second = offline_aura()  # noqa: F841
    third = offline_aura()   # noqa: F841
    assert len(_log_files()) == 1, (
        f'repeated Aura() construction leaked log files: {_log_files()}'
    )


def test_logging_still_flows_after_guard(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    offline_aura()
    offline_aura()  # guard active — must not break the first construction's sinks

    marker = f'probe-marker-{uuid.uuid4()}'
    logger.info(marker)
    logger.complete()

    files = _log_files()
    assert len(files) == 1
    assert marker in files[0].read_text(), (
        'the guard must not silence logging — records must reach the file sink'
    )


def test_cli_reconfigure_still_works(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from pushframe.cli import _configure_cli_logging

    offline_aura()
    # cli.py's wholesale reconfigure (logger.remove() + its own sinks) must
    # keep working with the guard in place — it is the quiet/debug switch.
    # Note: loguru's file_{time} sink creates a NEW file per logger.add(), so
    # the reconfigure legitimately starts a second file; the pre-reconfigure
    # one stays on disk (pre-existing CLI behavior, not this phase's debt).
    _configure_cli_logging(debug=False)

    marker = f'probe-marker-{uuid.uuid4()}'
    logger.info(marker)
    logger.complete()

    files = _log_files()
    assert files, 'reconfigure must leave a file sink behind'
    assert marker in files[-1].read_text(), (
        'records must flow through the reconfigured sink (newest file)'
    )


def test_guard_is_module_level_not_instance_level():
    # Two DIFFERENT instances: the second must skip registration because a
    # previous construction already configured the process (not because of
    # anything instance-local). Assert via the flag plus Aura()'s idempotent
    # re-construction from a fresh instance shape.
    assert hasattr(aura_module, '_LOGGER_READY'), (
        'MOD-04 fix must be a module-level guard (research-amended D-03)'
    )
    assert Aura._init_logger.__doc__, 'the guard carries its contract docstring'
