import os
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required

backup_bp = Blueprint('backup', __name__)


@backup_bp.route('/backups')
@login_required
def backup_list():
    from scripts.backup import list_backup_dicts, BACKUP_DIR, MAX_BACKUPS
    return render_template('backup/list.html',
                           backups=list_backup_dicts(),
                           backup_dir=BACKUP_DIR,
                           max_backups=MAX_BACKUPS)


@backup_bp.route('/api/backup/create', methods=['POST'])
@login_required
def create_backup_now():
    """Trigger an immediate backup."""
    try:
        from scripts.backup import create_backup
        dest = create_backup(verbose=True)
        stamp = os.path.basename(dest)[:-4]  # strip .zip
        return jsonify({'status': 'ok', 'stamp': stamp})
    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500


@backup_bp.route('/api/backup/restore', methods=['POST'])
@login_required
def restore_backup():
    """Restore from a named backup (stamp = YYYY-MM-DD_HH-MM-SS)."""
    data = request.get_json(force=True)
    stamp = data.get('stamp', '').strip()
    if not stamp:
        return jsonify({'status': 'error', 'message': 'No backup stamp provided'}), 400

    try:
        from scripts.backup import restore_backup as _restore
        safety = _restore(stamp, verbose=True)
        return jsonify({
            'status':  'ok',
            'message': f'Restored from {stamp}. Safety snapshot saved as {safety}. '
                       f'Restart the app.',
        })
    except FileNotFoundError:
        return jsonify({'status': 'error', 'message': f'Backup not found: {stamp}'}), 404
    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500
