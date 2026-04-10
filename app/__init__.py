import os
import threading
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'

# ── Hourly backup scheduler ────────────────────────────────────────────────────
_backup_timer: threading.Timer | None = None
_BACKUP_INTERVAL_SECONDS = 3600  # 1 hour


def _run_backup():
    """Execute one backup then reschedule."""
    global _backup_timer
    try:
        from scripts.backup import create_backup
        create_backup(verbose=True)
    except Exception as exc:
        print(f'[backup] ERROR during scheduled backup: {exc}')
    _backup_timer = threading.Timer(_BACKUP_INTERVAL_SECONDS, _run_backup)
    _backup_timer.daemon = True
    _backup_timer.start()


def start_backup_scheduler():
    """Start the hourly backup background thread (call once at app startup)."""
    global _backup_timer
    if _backup_timer is not None:
        return  # already running
    _backup_timer = threading.Timer(_BACKUP_INTERVAL_SECONDS, _run_backup)
    _backup_timer.daemon = True
    _backup_timer.start()
    print(f'[backup] Hourly backup scheduler started (interval: {_BACKUP_INTERVAL_SECONDS}s)')


def create_app(config_class=None):
    app = Flask(__name__)

    if config_class is None:
        from config import Config
        config_class = Config

    app.config.from_object(config_class)

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config.get('UNET_WEIGHTS_DIR', 'unet/weights'), exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # Register blueprints
    from app.routes.auth import auth_bp
    from app.routes.project import project_bp
    from app.routes.landmarks import landmarks_bp
    from app.routes.boundaries import boundaries_bp
    from app.routes.characters import characters_bp
    from app.routes.matrix import matrix_bp
    from app.routes.descriptions import descriptions_bp
    from app.routes.export import export_bp
    from app.routes.phylogeny import phylo_bp
    from app.routes.ai_advisor import ai_advisor_bp
    from app.routes.backup import backup_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(project_bp)
    app.register_blueprint(landmarks_bp)
    app.register_blueprint(boundaries_bp)
    app.register_blueprint(characters_bp)
    app.register_blueprint(matrix_bp)
    app.register_blueprint(descriptions_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(phylo_bp)
    app.register_blueprint(ai_advisor_bp)
    app.register_blueprint(backup_bp)

    with app.app_context():
        # Enable SQLite WAL mode and busy timeout to prevent "database is locked" errors
        from sqlalchemy import event
        @event.listens_for(db.engine, "connect")
        def set_sqlite_pragmas(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

        db.create_all()
        _migrate_phylogeny_jobs()

    # Start hourly backup scheduler (only in the main process, not reloader child)
    import sys
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        start_backup_scheduler()

    return app


def _migrate_phylogeny_jobs():
    """Add new columns to phylogeny_jobs without dropping existing data."""
    from sqlalchemy import text, inspect as sa_inspect
    inspector = sa_inspect(db.engine)
    if 'phylogeny_jobs' not in inspector.get_table_names():
        return
    existing = {c['name'] for c in inspector.get_columns('phylogeny_jobs')}
    new_cols = [
        ('ncbi_email',           'VARCHAR(200)'),
        ('target_taxon',         'VARCHAR(200)'),
        ('gene_query',           'TEXT'),
        ('min_length',           'INTEGER'),
        ('outgroup_definitions', 'TEXT'),
        ('bad_accessions',       'TEXT'),
        ('n_sequences_raw',      'INTEGER'),
        ('n_sequences_deduped',  'INTEGER'),
        ('n_sequences_final',    'INTEGER'),
        ('raw_fasta_path',       'VARCHAR(500)'),
        ('aligned_fasta_path',   'VARCHAR(500)'),
        ('trimmed_fasta_path',   'VARCHAR(500)'),
        ('max_length_factor',    'REAL'),
        ('nj_newick',            'TEXT'),
    ]
    # character_definitions migration
    if 'character_definitions' in inspector.get_table_names():
        cd_existing = {c['name'] for c in inspector.get_columns('character_definitions')}
        if 'display_order' not in cd_existing:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE character_definitions ADD COLUMN display_order INTEGER'))
                conn.commit()
    with db.engine.connect() as conn:
        for col, typ in new_cols:
            if col not in existing:
                conn.execute(text(f'ALTER TABLE phylogeny_jobs ADD COLUMN {col} {typ}'))
        conn.commit()
