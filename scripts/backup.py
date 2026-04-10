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
from datetime import datetime

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
BACKUP_DIR   = os.path.join(PROJECT_ROOT, 'project backup')
DB_PATH      = os.path.join(PROJECT_ROOT, 'data', 'db.sqlite')
UPLOADS_DIR  = os.path.join(PROJECT_ROOT, 'data', 'uploads')
MAX_BACKUPS  = 48   # hours of hourly backups to retain


def _timestamp() -> str:
    return datetime.now().strftime('%Y-%m-%d_%H-%M-%S')


def _all_backups() -> list[str]:
    """Return backup dirs sorted oldest-first."""
    pattern = os.path.join(BACKUP_DIR, '????-??-??_??-??-??')
    dirs = sorted(glob.glob(pattern))
    return dirs


def create_backup(verbose: bool = True) -> str:
    """Copy db.sqlite and uploads/ into a timestamped backup directory.

    Returns the path to the new backup directory.
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)

    stamp = _timestamp()
    dest  = os.path.join(BACKUP_DIR, stamp)
    os.makedirs(dest, exist_ok=True)

    # Copy database
    if os.path.exists(DB_PATH):
        shutil.copy2(DB_PATH, os.path.join(dest, 'db.sqlite'))
    else:
        if verbose:
            print(f'[backup] WARNING: database not found at {DB_PATH}')

    # Copy uploads directory
    uploads_dest = os.path.join(dest, 'uploads')
    if os.path.exists(UPLOADS_DIR):
        shutil.copytree(UPLOADS_DIR, uploads_dest)
    else:
        os.makedirs(uploads_dest, exist_ok=True)

    # Write manifest
    manifest = {
        'timestamp': stamp,
        'db_path':   DB_PATH,
        'uploads':   UPLOADS_DIR,
        'db_size':   os.path.getsize(os.path.join(dest, 'db.sqlite'))
                     if os.path.exists(os.path.join(dest, 'db.sqlite')) else 0,
    }
    with open(os.path.join(dest, 'manifest.json'), 'w') as fh:
        json.dump(manifest, fh, indent=2)

    if verbose:
        db_kb = manifest['db_size'] // 1024
        print(f'[backup] Created backup: {stamp}  (db {db_kb} KB)')

    prune_old_backups(verbose=verbose)
    return dest


def prune_old_backups(verbose: bool = True):
    """Remove oldest backups beyond the retention limit."""
    dirs = _all_backups()
    excess = dirs[:max(0, len(dirs) - MAX_BACKUPS)]
    for d in excess:
        shutil.rmtree(d, ignore_errors=True)
        if verbose:
            print(f'[backup] Pruned old backup: {os.path.basename(d)}')


def list_backups():
    """Print a table of available backups."""
    dirs = _all_backups()
    if not dirs:
        print('No backups found.')
        return
    print(f'{"#":<4} {"Timestamp":<22} {"DB size":>10}  Path')
    print('─' * 72)
    for i, d in enumerate(reversed(dirs), 1):
        manifest_path = os.path.join(d, 'manifest.json')
        db_size = '?'
        if os.path.exists(manifest_path):
            with open(manifest_path) as fh:
                m = json.load(fh)
            db_size = f'{m.get("db_size", 0) // 1024} KB'
        stamp = os.path.basename(d)
        print(f'{i:<4} {stamp:<22} {db_size:>10}  {d}')
    print(f'\n{len(dirs)} backup(s) stored (max {MAX_BACKUPS})')


if __name__ == '__main__':
    args = sys.argv[1:]
    if '--list' in args:
        list_backups()
    elif '--prune' in args:
        prune_old_backups()
    else:
        create_backup()
