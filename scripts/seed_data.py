"""First-boot data seeding for Railway (or any empty-volume deploy).

The DB and image uploads are gitignored, so a fresh deploy starts with an empty
DATA_DIR. If DATA_SEED_URL is set and the volume has no db.sqlite yet, download
that archive (a .zip of the `data/` folder) and extract it into DATA_DIR.

Expected archive layout (zip of the data/ directory contents):
    db.sqlite
    uploads/structures/...

Env vars:
    DATA_SEED_URL   public URL to data.zip (e.g. a GitHub Release asset)
    DATA_DIR        target dir (resolved by config.py; passed in explicitly here)

Safe + idempotent: does nothing if db.sqlite already exists on the volume.
"""
import os
import io
import sys
import zipfile
import urllib.request


def seed_if_empty(data_dir: str) -> bool:
    """Return True if a seed was performed, False otherwise."""
    db_path = os.path.join(data_dir, 'db.sqlite')
    force = os.environ.get('FORCE_SEED', '').strip() in ('1', 'true', 'True', 'yes')
    if os.path.exists(db_path) and not force:
        return False  # already populated — never overwrite live data
    if force and os.path.exists(db_path):
        print('[seed] FORCE_SEED set — overwriting existing data on the volume.')
        for stale in (db_path, db_path + '-wal', db_path + '-shm'):
            try:
                os.remove(stale)
            except OSError:
                pass

    url = os.environ.get('DATA_SEED_URL', '').strip()
    if not url:
        print('[seed] DATA_DIR empty and no DATA_SEED_URL set — starting fresh.')
        return False

    print(f'[seed] Empty volume detected. Downloading seed archive:\n        {url}')
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'gyro-seed'})
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read()
        print(f'[seed] Downloaded {len(raw) / 1e6:.1f} MB. Extracting…')
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            # If the zip wraps everything in a top-level "data/" folder, strip it.
            names = zf.namelist()
            strip = 'data/' if all(
                n.startswith('data/') for n in names if n and not n.startswith('__')
            ) else ''
            for member in zf.infolist():
                if member.is_dir():
                    continue
                rel = member.filename[len(strip):] if strip else member.filename
                if not rel or rel.startswith('__MACOSX'):
                    continue
                dest = os.path.join(data_dir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(member) as src, open(dest, 'wb') as out:
                    out.write(src.read())
        print('[seed] Seed complete.')
        return True
    except Exception as exc:
        print(f'[seed] ERROR seeding data: {exc}', file=sys.stderr)
        return False


if __name__ == '__main__':
    from config import DATA_DIR
    seed_if_empty(DATA_DIR)
