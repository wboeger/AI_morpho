import os
import json
import glob
from flask import Blueprint, render_template, redirect, url_for, flash, jsonify, request
from flask_login import login_required

backup_bp = Blueprint('backup', __name__)

BASE_DIR   = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
BACKUP_DIR = os.path.join(BASE_DIR, 'project backup')


def _all_backups() -> list[dict]:
    pattern = os.path.join(BACKUP_DIR, '????-??-??_??-??-??')
    dirs = sorted(glob.glob(pattern), reverse=True)  # newest first
    result = []
    for d in dirs:
        stamp = os.path.basename(d)
        manifest_path = os.path.join(d, 'manifest.json')
        db_size = 0
        if os.path.exists(manifest_path):
            with open(manifest_path) as fh:
                m = json.load(fh)
            db_size = m.get('db_size', 0)
        result.append({
            'stamp':   stamp,
            'path':    d,
            'db_kb':   db_size // 1024,
        })
    return result


@backup_bp.route('/backups')
@login_required
def backup_list():
    backups = _all_backups()
    return render_template('backup/list.html', backups=backups,
                           backup_dir=BACKUP_DIR)


@backup_bp.route('/api/backup/create', methods=['POST'])
@login_required
def create_backup_now():
    """Trigger an immediate backup."""
    try:
        from scripts.backup import create_backup
        dest = create_backup(verbose=True)
        stamp = os.path.basename(dest)
        return jsonify({'status': 'ok', 'stamp': stamp})
    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500


@backup_bp.route('/api/backup/restore', methods=['POST'])
@login_required
def restore_backup():
    """Restore from a named backup (stamp = YYYY-MM-DD_HH-MM-SS)."""
    data  = request.get_json(force=True)
    stamp = data.get('stamp', '').strip()

    if not stamp:
        return jsonify({'status': 'error', 'message': 'No backup stamp provided'}), 400

    backup_dir = os.path.join(BACKUP_DIR, stamp)
    if not os.path.isdir(backup_dir):
        return jsonify({'status': 'error', 'message': f'Backup not found: {stamp}'}), 404

    try:
        import shutil
        from datetime import datetime

        src_db      = os.path.join(backup_dir, 'db.sqlite')
        src_uploads = os.path.join(backup_dir, 'uploads')
        db_path     = os.path.join(BASE_DIR, 'data', 'db.sqlite')
        uploads_dir = os.path.join(BASE_DIR, 'data', 'uploads')

        # Safety snapshot before overwriting
        safety_stamp = 'pre-restore_' + datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        safety_dest  = os.path.join(BACKUP_DIR, safety_stamp)
        os.makedirs(safety_dest, exist_ok=True)
        if os.path.exists(db_path):
            shutil.copy2(db_path, os.path.join(safety_dest, 'db.sqlite'))
        if os.path.exists(uploads_dir):
            shutil.copytree(uploads_dir, os.path.join(safety_dest, 'uploads'))

        # Restore
        shutil.copy2(src_db, db_path)
        if os.path.exists(uploads_dir):
            shutil.rmtree(uploads_dir)
        if os.path.exists(src_uploads):
            shutil.copytree(src_uploads, uploads_dir)
        else:
            os.makedirs(uploads_dir, exist_ok=True)

        return jsonify({
            'status':  'ok',
            'message': f'Restored from {stamp}. Safety snapshot saved as {safety_stamp}. Restart the app.',
        })
    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500
