"""
backup.py — GyroMorpho v2 backup utility

Usage:
    python scripts/backup.py           # create one backup now
    python scripts/backup.py --list    # list available backups
    python scripts/backup.py --prune   # remove old backups beyond retention limit

Backups are stored in:
    <project_root>/project backup/YYYY-MM-DD_HH-MM-SS/

Each backup contains:
    db.sqlite        — copy of the database
    uploads/         — copy of all uploaded images

Retention: keeps the most recent MAX_BACKUPS backups (default 48 = 48 hours).
"""

import os
import sys
import shutil
import glob
import json
import zipfile
from datetime import datetime

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
# Honor the same data location as the app (Railway Volume / DATA_DIR override).
DATA_DIR     = (os.environ.get('DATA_DIR')
                or os.environ.get('RAILWAY_VOLUME_MOUNT_PATH')
                or os.path.join(PROJECT_ROOT, 'data'))
BACKUP_DIR   = os.path.join(DATA_DIR, 'project backup')
DB_PATH      = os.path.join(DATA_DIR, 'db.sqlite')
UPLOADS_DIR  = os.path.join(DATA_DIR, 'uploads')
MAX_BACKUPS  = 2    # keep the 2 most recent daily backups; prune the oldest


def _timestamp() -> str:
    return datetime.now().strftime('%Y-%m-%d_%H-%M-%S')


_STAMP_GLOB = '????-??-??_??-??-??'


def _all_backups() -> list[str]:
    """Return regular backup paths (zip files + legacy dirs) sorted oldest-first.

    Excludes pre-restore safety snapshots so they are never auto-pruned.
    """
    zips = glob.glob(os.path.join(BACKUP_DIR, _STAMP_GLOB + '.zip'))
    dirs = [d for d in glob.glob(os.path.join(BACKUP_DIR, _STAMP_GLOB))
            if os.path.isdir(d)]
    return sorted(zips + dirs, key=lambda p: os.path.basename(p)[:19])


def _write_backup_zip(zpath: str) -> int:
    """Write a compressed backup (db + uploads + manifest) to zpath.

    Returns the original (uncompressed) database size in bytes.
    """
    stamp = os.path.basename(zpath)[:-4]
    db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    tmp = zpath + '.part'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        if os.path.exists(DB_PATH):
            zf.write(DB_PATH, 'db.sqlite')
        if os.path.exists(UPLOADS_DIR):
            for root, _dirs, files in os.walk(UPLOADS_DIR):
                for f in files:
                    fp = os.path.join(root, f)
                    arc = os.path.join('uploads', os.path.relpath(fp, UPLOADS_DIR))
                    zf.write(fp, arc)
        manifest = {
            'timestamp': stamp,
            'db_path':   DB_PATH,
            'uploads':   UPLOADS_DIR,
            'db_size':   db_size,
        }
        zf.writestr('manifest.json', json.dumps(manifest, indent=2))
    os.replace(tmp, zpath)
    return db_size


def create_backup(verbose: bool = True) -> str:
    """Create a single compressed backup (<stamp>.zip) of db + uploads.

    Returns the path to the new backup zip.
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if not os.path.exists(DB_PATH) and verbose:
        print(f'[backup] WARNING: database not found at {DB_PATH}')

    stamp = _timestamp()
    zpath = os.path.join(BACKUP_DIR, stamp + '.zip')
    db_size = _write_backup_zip(zpath)

    if verbose:
        zip_kb = os.path.getsize(zpath) // 1024
        print(f'[backup] Created backup: {stamp}.zip  '
              f'(db {db_size // 1024} KB → zip {zip_kb} KB)')

    prune_old_backups(verbose=verbose)
    return zpath


def prune_old_backups(verbose: bool = True):
    """Remove oldest backups beyond the retention limit (zips and legacy dirs)."""
    items = _all_backups()
    excess = items[:max(0, len(items) - MAX_BACKUPS)]
    for p in excess:
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                os.remove(p)
            except OSError:
                pass
        if verbose:
            print(f'[backup] Pruned old backup: {os.path.basename(p)}')


def find_backup(stamp: str) -> str | None:
    """Locate a backup by timestamp — prefer the zip, fall back to a legacy dir."""
    z = os.path.join(BACKUP_DIR, stamp + '.zip')
    if os.path.exists(z):
        return z
    d = os.path.join(BACKUP_DIR, stamp)
    if os.path.isdir(d):
        return d
    return None


def restore_backup(stamp: str, verbose: bool = True) -> str:
    """Restore db + uploads from a named backup. Takes a pre-restore safety
    snapshot first (not pruned). Returns the safety snapshot basename."""
    src = find_backup(stamp)
    if not src:
        raise FileNotFoundError(f'Backup not found: {stamp}')

    os.makedirs(BACKUP_DIR, exist_ok=True)
    safety = os.path.join(BACKUP_DIR, 'pre-restore_' + _timestamp() + '.zip')
    _write_backup_zip(safety)   # safety snapshot — excluded from pruning

    # Replace current uploads wholesale.
    if os.path.exists(UPLOADS_DIR):
        shutil.rmtree(UPLOADS_DIR)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    if src.endswith('.zip'):
        with zipfile.ZipFile(src) as zf:
            names = zf.namelist()
            if 'db.sqlite' in names:
                with zf.open('db.sqlite') as s, open(DB_PATH, 'wb') as o:
                    shutil.copyfileobj(s, o)
            for n in names:
                if n.startswith('uploads/') and not n.endswith('/'):
                    dest = os.path.join(DATA_DIR, n)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with zf.open(n) as s, open(dest, 'wb') as o:
                        shutil.copyfileobj(s, o)
    else:  # legacy directory backup
        src_db = os.path.join(src, 'db.sqlite')
        if os.path.exists(src_db):
            shutil.copy2(src_db, DB_PATH)
        src_up = os.path.join(src, 'uploads')
        if os.path.exists(src_up):
            shutil.copytree(src_up, UPLOADS_DIR)
        else:
            os.makedirs(UPLOADS_DIR, exist_ok=True)

    # Clear any stale WAL/SHM from the replaced DB.
    for stale in (DB_PATH + '-wal', DB_PATH + '-shm'):
        try:
            os.remove(stale)
        except OSError:
            pass

    if verbose:
        print(f'[backup] Restored from {os.path.basename(src)} '
              f'(safety: {os.path.basename(safety)})')
    return os.path.basename(safety)


def list_backup_dicts() -> list[dict]:
    """Return backups (newest first) as dicts for the web UI."""
    result = []
    # Regular backups + pre-restore safety snapshots.
    paths = (glob.glob(os.path.join(BACKUP_DIR, _STAMP_GLOB + '.zip'))
             + [d for d in glob.glob(os.path.join(BACKUP_DIR, _STAMP_GLOB))
                if os.path.isdir(d)]
             + glob.glob(os.path.join(BACKUP_DIR, 'pre-restore_*.zip')))
    for p in sorted(set(paths), key=lambda x: os.path.basename(x).replace('.zip', ''),
                    reverse=True):
        base = os.path.basename(p)
        stamp = base[:-4] if base.endswith('.zip') else base
        db_size = 0
        try:
            if p.endswith('.zip'):
                with zipfile.ZipFile(p) as zf:
                    with zf.open('manifest.json') as fh:
                        db_size = json.load(fh).get('db_size', 0)
            else:
                mp = os.path.join(p, 'manifest.json')
                if os.path.exists(mp):
                    with open(mp) as fh:
                        db_size = json.load(fh).get('db_size', 0)
        except Exception:
            pass
        zip_kb = os.path.getsize(p) // 1024 if os.path.isfile(p) else 0
        result.append({'stamp': stamp, 'path': p,
                       'db_kb': db_size // 1024, 'zip_kb': zip_kb})
    return result


def list_backups():
    """Print a table of available backups."""
    items = list_backup_dicts()
    if not items:
        print('No backups found.')
        return
    print(f'{"#":<4} {"Timestamp":<24} {"DB":>9} {"Zip":>9}')
    print('─' * 50)
    for i, b in enumerate(items, 1):
        zk = f'{b["zip_kb"]} KB' if b['zip_kb'] else '—'
        print(f'{i:<4} {b["stamp"]:<24} {b["db_kb"]:>6} KB {zk:>9}')
    print(f'\n{len(items)} backup(s) stored (max {MAX_BACKUPS} + safety snapshots)')


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--list' in args:
        list_backups()
    elif '--prune' in args:
        prune_old_backups()
    else:
        create_backup()
