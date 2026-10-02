"""Phase 25 (MTF-01, D-01): the named-pair store.

Pairs live in config.json's `pairs` key as a NAMED dict:
    {"family": {"album": "family", "frame": "Living Room"}}
Names are operator-chosen and stable (referenced by --pair and by systemd
timers); insertion order is the --all execution order. Duplicate add fails
named; unknown resolution fails named LISTING the known names (PRF-02
style remedies).

Per-pair state (MTF-02) shards under the pair NAME (stable across album
re-shares, unlike share ids):
    manifest: ~/.config/pushframe/pairs/<name>/google-manifest.json
    cache:    ~/.local/state/pushframe/pairs/<name>/cache/
"""
from pathlib import Path

from pushframe import config_store


class PairError(Exception):
    """A named pair failure — message carries the remedy/lists names."""


def all_pairs() -> dict:
    """The pairs dict (insertion order = --all execution order). Empty when
    none configured."""
    return config_store.load().get('pairs', {}) or {}


def pair_add(name: str, *, album: str, frame: str) -> None:
    if not name or not name.strip():
        raise PairError('pair name must not be empty')
    stored = all_pairs()
    if name in stored:
        raise PairError(f'pair "{name}" already exists (album '
                        f'"{stored[name]["album"]}", frame '
                        f'"{stored[name]["frame"]}") — remove it first or '
                        f'choose another name')
    config_store.update(pairs={**stored, name: {'album': album,
                                                'frame': frame}})


def pair_remove(name: str) -> bool:
    """Remove a pair; True when it existed, False when already absent."""
    stored = all_pairs()
    if name not in stored:
        return False
    remaining = {k: v for k, v in stored.items() if k != name}
    config_store.update(pairs=remaining)
    return True


def pair_resolve(name: str) -> dict:
    """The pair's {album, frame} spec; unknown names list the known ones."""
    stored = all_pairs()
    if name not in stored:
        known = ', '.join(stored) if stored else '(none configured — '
        'add one with `pushframe config pair add`)'
        raise PairError(f'unknown pair "{name}". Known pairs: {known}')
    return stored[name]


def pair_list() -> None:
    """One line per pair, then the how-to-use footer.

    The bare mapping list explained nothing to a first-time operator (the
    2026-10-02 "pairing system is not easy to understand" report): the
    footer names the commands that consume a pair, so the output is
    self-sufficient. `pushframe config pair --help` carries the full map.
    """
    stored = all_pairs()
    if not stored:
        print('no pairs configured yet — add one with '
              '`pushframe config pair add <name> --album A --frame F`')
        print()
        print('Then run it by name (each pair keeps its own sync state):')
        print('  pushframe google-sync --pair <name>            # dry-run plan')
        print('  pushframe google-sync --pair <name> --apply    # mirror it')
        print('  pushframe google-sync --all                    # every pair, sorted')
        print('  pushframe schedule add nightly --pair <name> --every 1d')
        return
    count = len(stored)
    print(f'{count} pair{"s" if count > 1 else ""}:')
    for name, spec in stored.items():
        print(f'  {name}: album "{spec["album"]}" → frame "{spec["frame"]}"')
    print()
    first = next(iter(stored))
    print('Run a pair by name (each pair keeps its own sync state):')
    print(f'  pushframe google-sync --pair {first}            # dry-run plan')
    print(f'  pushframe google-sync --pair {first} --apply    # mirror it')
    print('  pushframe google-sync --all                    # every pair, sorted')
    print('  pushframe schedule add nightly --pair <name> --every 1d')


def pair_state_paths(name: str) -> tuple[Path, Path]:
    """(manifest, cache) shard paths for a pair (MTF-02). Derived from the
    pair NAME — stable across album re-shares."""
    manifest = Path('~/.config/pushframe').expanduser() / 'pairs' / name \
        / 'google-manifest.json'
    cache = Path('~/.local/state/pushframe').expanduser() / 'pairs' / name \
        / 'cache'
    return manifest, cache
