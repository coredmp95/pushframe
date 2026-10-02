"""Phase 25 (MTF-01): the named-pair store.

Pairs live in config.json's `pairs` key as a NAMED dict (D-01):
    {"cadre-venus": {"album": "Cadre", "frame": "Cadre de Fabrice"}}
Names are operator-chosen and stable; --pair <name> and timers reference
them. Duplicate add = named error (never silently overwrite). Unknown
resolution = named error LISTING the known names.
"""
import json

import pytest

from pushframe.utils import settings
from pushframe import config_store
from pushframe import pairs as pairs_mod


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    return path


def test_add_list_roundtrip(cfg_path, capsys):
    pairs_mod.pair_add("cadre-venus", album="Cadre", frame="Cadre de Fabrice")
    pairs_mod.pair_add("salon", album="Famille", frame="Salon")
    data = json.loads(cfg_path.read_text())
    assert data["pairs"]["cadre-venus"] == {"album": "Cadre",
                                            "frame": "Cadre de Fabrice"}
    out = capsys.readouterr().out
    pairs_mod.pair_list()
    out = capsys.readouterr().out
    assert "cadre-venus" in out and "Cadre de Fabrice" in out
    assert "salon" in out
    # The footer names the consuming commands — a bare mapping list
    # explained nothing (2026-10-02 report).
    assert "2 pairs:" in out
    assert "google-sync --pair cadre-venus" in out
    assert "google-sync --all" in out
    assert "schedule add nightly --pair" in out


def test_list_empty_explains_how_to_add_and_use(cfg_path, capsys):
    pairs_mod.pair_list()
    out = capsys.readouterr().out
    assert "no pairs configured" in out
    assert "config pair add <name> --album A --frame F" in out
    assert "google-sync --pair <name>" in out
    assert "schedule add nightly --pair <name>" in out


def test_pair_help_and_unknown_subcommand(cfg_path, capsys):
    """`config pair --help` maps the pair system; a typo exits 2 with the
    same pointer (it used to print one usage line and exit 1)."""
    from pushframe.cli import run_config
    assert run_config(wizard_args=["pair", "--help"], stdin_isatty=True) == 0
    out = capsys.readouterr().out
    assert "usage: pushframe config pair list" in out
    assert "A pair is a named album↔frame mapping" in out
    assert "google-sync --pair family" in out
    assert "docs/CLI.md#pairs" in out

    assert run_config(wizard_args=["pair", "shwo"], stdin_isatty=True) == 2
    out = capsys.readouterr().out
    assert "usage: pushframe config pair list" in out
    assert "config pair --help" in out


def test_gsync_pair_parses_without_positional_album(cfg_path, monkeypatch, capsys):
    """`pushframe google-sync --pair name --apply` must parse without the
    positional album — the pair supplies it. It used to die in argparse
    with "the following arguments are required: album", which forced the
    confusing `google-sync "Album X" --pair name` workaround."""
    from pushframe import gsync as gsync_mod
    captured = {}

    def fake_run(album_target, frame_arg, **kw):
        captured['album'] = album_target
        captured['frame'] = frame_arg
        captured.update(kw)
        return 0

    monkeypatch.setattr(gsync_mod, 'run_google_sync', fake_run)
    from pushframe.cli import main
    rc = main(['google-sync', '--pair', 'cadre-venus', '--apply'])
    assert rc == 0
    assert captured['album'] is None
    assert captured['pair'] == 'cadre-venus'
    assert captured['apply'] is True
    assert 'note:' not in capsys.readouterr().out

    # a positional album WITH --pair is accepted but named as ignored
    rc = main(['google-sync', 'Album X', '--pair', 'cadre-venus'])
    assert rc == 0
    assert 'note: with --pair/--all' in capsys.readouterr().out

    # nothing to resolve at all: usage error naming all three ways
    rc = main(['google-sync'])
    assert rc == 2
    out = capsys.readouterr().out
    assert '--pair NAME' in out and '--all' in out


def test_add_duplicate_is_named_error(cfg_path):
    pairs_mod.pair_add("cadre-venus", album="Cadre", frame="F1")
    with pytest.raises(pairs_mod.PairError) as exc:
        pairs_mod.pair_add("cadre-venus", album="Cadre", frame="F2")
    assert "already exists" in str(exc.value)
    # the original pair is untouched
    assert config_store.load()["pairs"]["cadre-venus"]["frame"] == "F1"


def test_remove_existing_and_missing(cfg_path):
    pairs_mod.pair_add("cadre-venus", album="Cadre", frame="F1")
    assert pairs_mod.pair_remove("cadre-venus") is True
    assert config_store.load()["pairs"] == {}
    assert pairs_mod.pair_remove("cadre-venus") is False  # idempotent-ish


def test_resolve_known_and_unknown(cfg_path):
    pairs_mod.pair_add("cadre-venus", album="Cadre", frame="F1")
    spec = pairs_mod.pair_resolve("cadre-venus")
    assert spec == {"album": "Cadre", "frame": "F1"}
    with pytest.raises(pairs_mod.PairError) as exc:
        pairs_mod.pair_resolve("nope")
    # the error LISTS the known names (PRF-02 remedy style)
    assert "cadre-venus" in str(exc.value)


def test_state_paths_are_sharded_by_pair_name(cfg_path, tmp_path):
    pairs_mod.pair_add("cadre-venus", album="Cadre", frame="F1")
    m, c = pairs_mod.pair_state_paths("cadre-venus")
    assert "pairs" in str(m) and "cadre-venus" in str(m)
    assert m.name == "google-manifest.json"
    assert "cadre-venus" in str(c) and c.name == "cache"


def test_all_pairs_order_is_deterministic_sorted(cfg_path):
    """--all execution order is SORTED (deterministic across saves — the
    config writer sorts keys for stable diffs). Documented contract: pair
    order in --all is alphabetical by name."""
    pairs_mod.pair_add("b-pair", album="A2", frame="F2")
    pairs_mod.pair_add("a-pair", album="A1", frame="F1")
    names = list(pairs_mod.all_pairs())
    assert names == ["a-pair", "b-pair"]


def test_cli_config_pair_dispatch(cfg_path, capsys, monkeypatch):
    """`pushframe config pair add/list/remove` reaches the store."""
    from pushframe.cli import run_config
    assert run_config(wizard_args=["pair", "add", "p1", "--album", "A",
                                    "--frame", "F"], stdin_isatty=True) == 0
    assert run_config(wizard_args=["pair", "list"], stdin_isatty=True) == 0
    assert "p1" in capsys.readouterr().out
    assert run_config(wizard_args=["pair", "remove", "p1"],
                      stdin_isatty=True) == 0
    assert config_store.load()["pairs"] == {}


# --- pair add dispatch: malformed tails are named errors, never crashes ------

def test_pair_add_dangling_flag_is_a_named_error_not_a_crash(cfg_path,
                                                             capsys):
    """Audit 2026-10-02: `config pair add p1 --album A --frame` died with a
    raw IndexError — same family as the `schedule add --pair` venus
    crash fixed in 5.1.22."""
    from pushframe.cli import run_config
    assert run_config(wizard_args=["pair", "add", "p1", "--album", "A",
                                   "--frame"], stdin_isatty=True) == 2
    out = capsys.readouterr().out
    assert "IndexError" not in out
    assert "--frame needs a value" in out
    assert "config pair --help" in out


def test_pair_add_flag_in_name_slot_is_refused(cfg_path, capsys):
    """Audit 2026-10-02, live-confirmed: `pair add --album A --frame F`
    silently created a pair literally named `--album`."""
    from pushframe.cli import run_config
    assert run_config(wizard_args=["pair", "add", "--album", "A",
                                   "--frame", "F"], stdin_isatty=True) == 2
    out = capsys.readouterr().out
    assert "pair name is missing" in out
    assert "--album" not in config_store.load().get("pairs", {})


def test_pair_add_unknown_and_duplicated_flags_are_named_errors(cfg_path,
                                                                capsys):
    from pushframe.cli import run_config
    assert run_config(wizard_args=["pair", "add", "p1", "--albun", "A",
                                   "--frame", "F"], stdin_isatty=True) == 2
    out = capsys.readouterr().out
    assert 'unknown option "--albun"' in out
    capsys.readouterr()                         # reset
    assert run_config(wizard_args=["pair", "add", "p1", "--album", "A",
                                   "--album", "B", "--frame", "F"],
                      stdin_isatty=True) == 2
    assert "--album given twice" in capsys.readouterr().out


def test_pair_add_missing_required_flag_is_a_named_error(cfg_path, capsys):
    from pushframe.cli import run_config
    assert run_config(wizard_args=["pair", "add", "p1", "--album", "A"],
                      stdin_isatty=True) == 2
    out = capsys.readouterr().out
    assert "--frame is missing" in out
    assert config_store.load().get("pairs", {}) == {}


def test_pair_remove_flag_is_a_named_error(cfg_path, capsys):
    from pushframe.cli import run_config
    assert run_config(wizard_args=["pair", "remove", "--now"],
                      stdin_isatty=True) == 2
    out = capsys.readouterr().out
    assert "pair remove: needs a pair name" in out
