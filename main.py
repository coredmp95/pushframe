import os
import sys

from dotenv import load_dotenv

from pushframe.aura import Aura
from pushframe import export


def _is_image_asset(asset) -> bool:
    """An asset we can safely download + EXIF-stamp: it has a thumbnail URL and
    is not a video / live photo (mirrors tests/test_read_path.py:10-14)."""
    return bool(asset.thumbnail_url) and not asset.video_url and not asset.is_live


def main():
    # Load AURA_EMAIL / AURA_PASSWORD from a local .env so the read-path demo can
    # run without exporting shell vars. Shell-exported vars still win (override
    # defaults to False), and a missing .env is a no-op — so a credential-less
    # checkout still prints the guard and exits cleanly below.
    load_dotenv()

    # Credential guard (D-07): mirror the Phase 2 test skip philosophy, but exit
    # cleanly instead of skipping. Aura.login() already defaults its args to these
    # same env vars (aura.py:36), so we only need to detect-and-message here.
    if not os.getenv('AURA_EMAIL') or not os.getenv('AURA_PASSWORD'):
        print(
            'AURA_EMAIL / AURA_PASSWORD not set — export them (or add a local .env '
            'used by the test path) to run the read-path demo.'
        )
        sys.exit(0)

    # Read path via existing Aura facade methods only (D-07 — no new client logic).
    aura = Aura()
    aura.login()                                    # READ-01

    frames = aura.frame_api.get_frames()            # READ-02 (first frame)
    if not frames:
        print('No frames on this account — nothing to read.')
        sys.exit(0)
    frame = frames[0]

    assets = aura.get_all_assets(frame.id)          # READ-03 (cursor loop)
    if not assets:
        print(f'Frame {frame.name} ({frame.id}) has no assets — nothing to download.')
        sys.exit(0)

    # Asset selection fallback chain (test_read_path.py:79-94): first downloadable
    # image asset with a location (exercises GPS) → else first image asset → else
    # first asset. `assets` is non-empty here, so the final `[0]` is safe.
    image_assets = [a for a in assets if _is_image_asset(a)]
    geo_image_assets = [a for a in image_assets if a.location_name]
    asset = (geo_image_assets or image_assets or assets)[0]

    out_dir = 'asset_images/'
    os.makedirs(out_dir, exist_ok=True)             # gitignored output dir

    # get_image_from_asset does not return the saved path. Snapshot the dir
    # before/after so we report the file THIS download actually produced, not
    # whatever sorts last in a directory that may hold images from prior runs.
    before = set(os.listdir(out_dir))
    export.get_image_from_asset(asset, out_dir, aura.exif_writer)  # READ-04
    saved = sorted(os.path.join(out_dir, f) for f in set(os.listdir(out_dir)) - before)

    # Concise summary — non-secret values only (never the password or auth token).
    print('Read-path demo complete:')
    print(f'  Frame:          {frame.name} ({frame.id})')
    print(f'  Total assets:   {len(assets)}')
    print(f'  Selected asset: {asset.id}')
    print(f'  Saved image(s): {", ".join(saved) if saved else f"(none new in {out_dir})"}')


if __name__ == '__main__':
    main()
