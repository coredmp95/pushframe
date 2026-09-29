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
