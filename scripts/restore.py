"""
restore.py — GyroMorpho v2 restore utility

Usage:
    python scripts/restore.py               # interactive: pick backup from list
    python scripts/restore.py --latest      # restore the most recent backup
    python scripts/restore.py --backup 2025-04-08_10-00-00  # restore by timestamp

What it does:
  1. Stops you from accidentally overwriting a running app (asks for confirmation).
  2. Copies the chosen backup's db.sqlite over data/db.sqlite.
  3. Replaces data/uploads/ with the backup's uploads/.
  4. Creates a 'pre-restore' safety snapshot of the current state first.
"""

import os
import sys
import shutil
import json
import glob
from datetime import datetime

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
BACKUP_DIR   = os.path.join(PROJECT_ROOT, 'project backup')
DB_PATH      = os.path.join(PROJECT_ROOT, 'data', 'db.sqlite')
UPLOADS_DIR  = os.path.join(PROJECT_ROOT, 'data', 'uploads')


def _all_backups() -> list[str]:
    pattern = os.path.join(BACKUP_DIR, '????-??-??_??-??-??')
    return sorted(glob.glob(pattern))


def _safety_snapshot():
    """Save current state as a 'pre-restore_<stamp>' backup before overwriting."""
    stamp = 'pre-restore_' + datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    dest  = os.path.join(BACKUP_DIR, stamp)
    os.makedirs(dest, exist_ok=True)
    if os.path.exists(DB_PATH):
        shutil.copy2(DB_PATH, os.path.join(dest, 'db.sqlite'))
    if os.path.exists(UPLOADS_DIR):
        shutil.copytree(UPLOADS_DIR, os.path.join(dest, 'uploads'))
    print(f'[restore] Safety snapshot saved: {stamp}')
    return dest


def restore_backup(backup_dir: str, yes: bool = False):
    """Restore from backup_dir into data/."""
    if not os.path.isdir(backup_dir):
        print(f'ERROR: backup directory not found: {backup_dir}')
        sys.exit(1)

    stamp = os.path.basename(backup_dir)
    src_db      = os.path.join(backup_dir, 'db.sqlite')
    src_uploads = os.path.join(backup_dir, 'uploads')

    if not os.path.exists(src_db):
        print(f'ERROR: no db.sqlite in backup {stamp}')
        sys.exit(1)

    print(f'\nAbout to restore from backup: {stamp}')
    print(f'  Database : {src_db}')
    print(f'  Uploads  : {src_uploads}')
    print(f'\nThis will OVERWRITE:')
    print(f'  {DB_PATH}')
    print(f'  {UPLOADS_DIR}/')

    if not yes:
        answer = input('\nContinue? [y/N] ').strip().lower()
        if answer != 'y':
            print('Aborted.')
            return

    _safety_snapshot()

    # Restore database
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    shutil.copy2(src_db, DB_PATH)
    print(f'[restore] Database restored.')

    # Restore uploads
    if os.path.exists(UPLOADS_DIR):
        shutil.rmtree(UPLOADS_DIR)
    if os.path.exists(src_uploads):
        shutil.copytree(src_uploads, UPLOADS_DIR)
    else:
        os.makedirs(UPLOADS_DIR, exist_ok=True)
    print(f'[restore] Uploads restored.')

    print(f'\n[restore] Done. Restart the Flask app to use the restored database.')


def pick_interactively() -> str:
    dirs = _all_backups()
    if not dirs:
        print('No backups available.')
        sys.exit(0)

    print(f'\n{"#":<4} {"Timestamp":<25} {"DB size":>10}')
    print('─' * 45)
    for i, d in enumerate(reversed(dirs), 1):
        manifest_path = os.path.join(d, 'manifest.json')
        db_size = '?'
        if os.path.exists(manifest_path):
            with open(manifest_path) as fh:
                m = json.load(fh)
            db_size = f'{m.get("db_size", 0) // 1024} KB'
        print(f'{i:<4} {os.path.basename(d):<25} {db_size:>10}')

    print()
    try:
        choice = int(input(f'Enter backup number to restore [1-{len(dirs)}]: ').strip())
        if choice < 1 or choice > len(dirs):
            raise ValueError
    except (ValueError, KeyboardInterrupt):
        print('Invalid choice. Aborted.')
        sys.exit(1)

    # reversed list: choice 1 = most recent
    return list(reversed(dirs))[choice - 1]


if __name__ == '__main__':
    args = sys.argv[1:]
    yes  = '--yes' in args or '-y' in args

    if '--latest' in args:
        dirs = _all_backups()
        if not dirs:
            print('No backups found.')
            sys.exit(1)
        restore_backup(dirs[-1], yes=yes)

    elif '--backup' in args:
        idx = args.index('--backup')
        if idx + 1 >= len(args):
            print('ERROR: --backup requires a timestamp argument')
            sys.exit(1)
        stamp = args[idx + 1]
        backup_dir = os.path.join(BACKUP_DIR, stamp)
        restore_backup(backup_dir, yes=yes)

    else:
        backup_dir = pick_interactively()
        restore_backup(backup_dir, yes=yes)
