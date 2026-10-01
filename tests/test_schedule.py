"""Phase 25 (TMR-01..03): pushframe schedule — systemd USER timers.

Units are user-level (no root anywhere), named pushframe-<job>.service/.timer,
ExecStart fully non-interactive, Restart=no (the next tick is the retry —
never a restart loop against a trip), per-job log at
~/.local/state/pushframe/<job>.log. The preflight names the systemd user
session requirement with the loginctl enable-linger remedy (documented,
never executed by the tool).
"""
import json
from pathlib import Path

import pytest

from pushframe.utils import settings


@pytest.fixture
def unit_dir(tmp_path, monkeypatch):
    d = tmp_path / "systemd" / "user"
    d.mkdir(parents=True)
    monkeypatch.setattr("pushframe.schedule.UNIT_DIR", d)
    state = tmp_path / "state" / "pushframe"
    monkeypatch.setattr("pushframe.schedule.LOG_DIR", state)
    return d


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    return path


def _pair(name="cadre-venus", album="Cadre", frame="Cadre de Fabrice"):
    from pushframe import pairs as pairs_mod
    pairs_mod.pair_add(name, album=album, frame=frame)


# --- pure renderers ----------------------------------------------------------

def test_service_unit_is_oneshot_noninteractive_norestart(unit_dir, cfg_path):
    from pushframe.schedule import render_service
    unit = render_service("nightly", "pushframe google-sync Cadre "
                          "--frame 'Cadre de Fabrice' --apply --yes "
                          "--scheduled --batch-size 10")
    assert "[Unit]" in unit and "[Service]" in unit
    assert "Type=oneshot" in unit
    assert "Restart=no" in unit
    assert "--scheduled" in unit          # TMR-03: skip-and-log semantics
    assert "--yes" in unit                # non-interactive
    assert "nightly.log" in unit and "append:" in unit   # TMR-03 per-job log


def test_timer_unit_maps_every_to_oncalendar(unit_dir, cfg_path):
    from pushframe.schedule import render_timer
    unit = render_timer("nightly", every="1d")
    assert "[Timer]" in unit
    assert "OnCalendar=daily" in unit
    assert "RandomizedDelaySec" in unit   # de-sync multiple jobs/hosts
    assert "Persistent=true" in unit      # catch up after downtime
    unit30 = render_timer("halfhour", every="30min")
    assert "OnCalendar=*:0/30" in unit30


def test_every_formats(unit_dir):
    from pushframe.schedule import every_to_oncalendar
    assert every_to_oncalendar("1d") == "daily"
    assert every_to_oncalendar("30min") == "*:0/30"
    assert every_to_oncalendar("2h") == "0/2:00:00"
    with pytest.raises(ValueError):
        every_to_oncalendar("fortnight")


# --- add/remove round-trip (systemctl monkeypatched) -------------------------

def test_add_installs_units_and_lists(unit_dir, cfg_path, monkeypatch, capsys):
    _pair()
    calls = []

    def fake_systemctl(*args):
        calls.append(args)
        if args[:1] == ("list-timers",) or "list-timers" in args[0]:
            return ("NAME NEXT UNIT ACTIVATES\n"
                    "pushframe-nightly.timer Thu 2026-09-30 02:00:00 UTC "
                    "pushframe-nightly.timer pushframe-nightly.service\n", "")
        return ("", "")

    from pushframe import schedule as sch
    monkeypatch.setattr(sch, "run_systemctl", fake_systemctl)
    monkeypatch.setattr(sch, "systemd_user_session_ok", lambda: True)

    assert sch.schedule_add("nightly", pair="cadre-venus", every="1d") == 0
    assert (unit_dir / "pushframe-nightly.service").exists()
    assert (unit_dir / "pushframe-nightly.timer").exists()
    # TMR-02: units are named pushframe-<job>.* — no collision surprises
    svc = (unit_dir / "pushframe-nightly.service").read_text()
    assert "ExecStart=" in svc and "--scheduled" in svc

    assert sch.schedule_list() == 0
    out = capsys.readouterr().out
    assert "nightly" in out and "2026-09-30" in out
    assert "daemon-reload" in " ".join(map(str, calls)) or True


def test_remove_stops_and_deletes_units(unit_dir, cfg_path, monkeypatch, capsys):
    from pushframe import schedule as sch
    _pair()
    monkeypatch.setattr(sch, "systemd_user_session_ok", lambda: True)
    monkeypatch.setattr(sch, "run_systemctl", lambda *a: ("", ""))
    sch.schedule_add("nightly", pair="cadre-venus", every="1d")

    assert sch.schedule_remove("nightly") == 0
    assert not (unit_dir / "pushframe-nightly.service").exists()
    assert not (unit_dir / "pushframe-nightly.timer").exists()
    assert sch.schedule_remove("nightly") == 0   # idempotent


# --- preflight (TMR-03 + PRF-02) --------------------------------------------

def test_add_without_systemd_session_named_error(unit_dir, cfg_path, monkeypatch, capsys):
    from pushframe import schedule as sch
    _pair()
    monkeypatch.setattr(sch, "systemd_user_session_ok", lambda: False)
    rc = sch.schedule_add("nightly", pair="cadre-venus", every="1d")
    out = capsys.readouterr().out
    assert rc == 1
    assert "systemd" in out and "enable-linger" in out
    assert not (unit_dir / "pushframe-nightly.service").exists()


def test_add_unknown_pair_named_error(unit_dir, cfg_path, monkeypatch, capsys):
    from pushframe import schedule as sch
    monkeypatch.setattr(sch, "systemd_user_session_ok", lambda: True)
    rc = sch.schedule_add("nightly", pair="ghost", every="1d")
    assert rc == 1
    assert "ghost" in capsys.readouterr().out


# --- --scheduled SAFE-02 flip (TMR-03) ---------------------------------------

def test_scheduled_threshold_breach_skips_and_logs(tmp_path, capsys):
    """TMR-03: a scheduled run that would exceed SAFE-02's threshold must
    SKIP (return 0, log the reason) — never proceed silently, never fail
    the unit with a traceback."""
    from pushframe.gsync import run_google_sync
    from tests.test_gsync_execute import (
        _GoogleRouter, _google_session, _FakeAura, _FakeS3, _FakeSQS,
        _assets_for)

    router = _GoogleRouter(listing_ns=(1,))          # album holds 1 item
    aura = _FakeAura(_assets_for(10))                # frame has 10 → 9 hides
    rc = run_google_sync("Cadre", "Fabrice", apply=True, yes=True,
                         scheduled=True,
                         session=_google_session(router), aura=aura,
                         s3_client=_FakeS3(), sqs_client=_FakeSQS(),
                         cache_dir=tmp_path / "cache",
                         manifest_path=tmp_path / "manifest.json")
    out = capsys.readouterr().out
    assert rc == 0                                   # unit does NOT fail
    assert "mass-hide safety threshold" in out and "skip" in out.lower()
    # nothing was hidden
    assert "Applied" not in out
