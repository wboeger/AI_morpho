import os
import threading
from datetime import datetime, timedelta
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'

# ── Daily backup scheduler (one backup/day at noon; keep 5 most recent) ─────────
_backup_timer: threading.Timer | None = None
_BACKUP_HOUR = 12  # local-time hour for the daily backup (noon)


def _seconds_until_next_run() -> float:
    """Seconds from now until the next _BACKUP_HOUR:00 local time."""
    now = datetime.now()
    target = now.replace(hour=_BACKUP_HOUR, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _run_backup():
    """Execute one daily backup then reschedule for the next noon."""
    global _backup_timer
    try:
        from scripts.backup import create_backup
        create_backup(verbose=True)
    except Exception as exc:
        print(f'[backup] ERROR during scheduled backup: {exc}')
    delay = _seconds_until_next_run()
    _backup_timer = threading.Timer(delay, _run_backup)
    _backup_timer.daemon = True
    _backup_timer.start()


def start_backup_scheduler():
    """Start the daily backup background thread (call once at app startup).

    Fires once per day at _BACKUP_HOUR local time; retention is handled by
    scripts.backup (keeps the 5 most recent, prunes the oldest).
    """
    global _backup_timer
    if _backup_timer is not None:
        return  # already running
    delay = _seconds_until_next_run()
    _backup_timer = threading.Timer(delay, _run_backup)
    _backup_timer.daemon = True
    _backup_timer.start()
    next_run = (datetime.now() + timedelta(seconds=delay)).strftime('%Y-%m-%d %H:%M')
    print(f'[backup] Daily backup scheduler started (next run: {next_run}, keep 2)')


def create_app(config_class=None):
    app = Flask(__name__)

    if config_class is None:
        from config import Config
        config_class = Config

    app.config.from_object(config_class)

    # Seed the data volume on first boot (no-op if db.sqlite already present or
    # DATA_SEED_URL unset). Must run before makedirs/db so the download lands.
    try:
        from scripts.seed_data import seed_if_empty
        seed_if_empty(app.config['DATA_DIR'])
    except Exception as exc:
        print(f'[seed] skipped: {exc}')

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config.get('UNET_WEIGHTS_DIR', 'unet/weights'), exist_ok=True)
    _sync_docs(app)

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
    from app.routes.optimization import optimization_bp

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
    app.register_blueprint(optimization_bp)

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
        _migrate_a02_states()
        _migrate_character_states()
        _migrate_a06_states()
        _migrate_a06_names()
        _migrate_structures()
        _migrate_specimens()
        _migrate_users()
        _backfill_no_image_unknown()
        _ensure_admin()

    # Start hourly backup scheduler (only in the main process, not reloader child).
    # Disabled by default on Railway/production via ENABLE_BACKUPS=0 to avoid
    # filling the volume with hourly full-image copies.
    import sys
    backups_on = os.environ.get('ENABLE_BACKUPS', '1') not in ('0', 'false', 'False')
    if backups_on and (not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true'):
        start_backup_scheduler()

    return app


DOC_FILES = [
    'GyroMorpho_QuickStart_Guide.pdf',
    'GyroMorpho_v2_Manual.pdf',
]


def _sync_docs(app):
    """Copy the bundled PDF manuals from the repo into the persistent volume.

    The PDFs ship inside the image (repo root). We copy them into
    DATA_DIR/docs on the volume so they survive and are served from there.
    Re-copies only when the repo file is newer or the volume copy is missing,
    so manuals regenerated and redeployed get refreshed automatically.
    """
    import shutil
    src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    docs_dir = app.config['DOCS_FOLDER']
    try:
        os.makedirs(docs_dir, exist_ok=True)
        for name in DOC_FILES:
            src = os.path.join(src_dir, name)
            dst = os.path.join(docs_dir, name)
            if not os.path.exists(src):
                continue
            if (not os.path.exists(dst) or
                    os.path.getmtime(src) > os.path.getmtime(dst)):
                shutil.copy2(src, dst)
    except Exception as exc:
        print(f'[docs] sync skipped: {exc}')


def _migrate_users():
    """Add the users.active column if missing (defaults existing users to active)."""
    from sqlalchemy import inspect as sa_inspect, text
    inspector = sa_inspect(db.engine)
    if 'users' not in inspector.get_table_names():
        return
    cols = [c['name'] for c in inspector.get_columns('users')]
    if 'active' not in cols:
        db.session.execute(text('ALTER TABLE users ADD COLUMN active BOOLEAN DEFAULT 1'))
        db.session.execute(text('UPDATE users SET active = 1 WHERE active IS NULL'))
        db.session.commit()
        print('[migrate] users.active column added.')


def _backfill_no_image_unknown():
    """Code characters as '?' for structures flagged no_image that have no value
    yet. Idempotent and add-only: never overwrites an existing coded value."""
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(db.engine)
    if 'structures' not in inspector.get_table_names():
        return
    from app.models import Structure, CharacterDefinition, CharacterValue, Specimen
    from app.routes.project import _NO_IMAGE_REASON
    from datetime import datetime, timezone
    added = 0
    for st in Structure.query.filter_by(no_image=True).all():
        sp = db.session.get(Specimen, st.specimen_id)
        if not sp:
            continue
        chars = CharacterDefinition.query.filter_by(
            project_id=sp.project_id, structure_type=st.structure_type, active=True).all()
        for ch in chars:
            exists = CharacterValue.query.filter_by(
                structure_id=st.id, character_id=ch.id).first()
            if exists:
                continue
            db.session.add(CharacterValue(
                structure_id=st.id, character_id=ch.id, state='?',
                confidence=0.0, auto_assigned=True,
                override_reason=_NO_IMAGE_REASON,
                override_at=datetime.now(timezone.utc)))
            added += 1
    if added:
        db.session.commit()
        print(f'[migrate] Coded {added} no-image character value(s) as "?".')


def _ensure_admin():
    """Ensure an administrator account exists.

    On a fresh database (e.g. a new Railway volume without a seeded db), create
    the admin from ADMIN_USERNAME / ADMIN_PASSWORD env vars. Idempotent: only
    creates the user if it does not already exist; never changes an existing
    password here.
    """
    from app.models import User
    username = os.environ.get('ADMIN_USERNAME')
    password = os.environ.get('ADMIN_PASSWORD')
    if not username or not password:
        return
    if User.query.filter_by(username=username).first():
        return
    admin = User(username=username,
                 email=os.environ.get('ADMIN_EMAIL', f'{username}@local'),
                 role='admin')
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    print(f'[auth] Bootstrapped admin account "{username}".')


def _migrate_a02_states():
    """Update A02 states in all existing projects to the corrected definitions.

    Old: 0 (<60°), 2 (60–120°), 1 (>120°) — codes out of order, vague names.
    New: 0 (<45°), 1 (45–90°), 2 (>90°) — monotonic codes, anatomical names.
    """
    import json
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(db.engine)
    if 'character_definitions' not in inspector.get_table_names():
        return

    new_states = [
        {'code': '0', 'name': 'slightly curved',
         'description': 'Point departs only slightly from shaft axis; acute exterior angle < 30°',
         'threshold_min': None, 'threshold_max': 30},
        {'code': '1', 'name': 'moderately curved',
         'description': 'Distinct bend at point–shaft junction; typical hook shape (30°–60°)',
         'threshold_min': 30, 'threshold_max': 60},
        {'code': '2', 'name': 'strongly curved',
         'description': 'Sharp bend approaching a right angle; acute exterior angle > 60°',
         'threshold_min': 60, 'threshold_max': None},
    ]
    new_formula = ('acute exterior angle between middle-third shaft midline and point midline '
                   '(base midpoint to tip); 0°=straight, 90°=right-angle bend')

    from app.models import CharacterDefinition, CharacterValue
    from app.characters import map_value_to_state
    any_changed = False
    for char in CharacterDefinition.query.filter_by(code='A02').all():
        # Always ensure states_json is the current definition
        char.states_json = new_states
        char.formula = new_formula

        # Convert old bend-angle raw_values (>90°) to acute exterior angle,
        # then remap states to new thresholds.
        remapped = 0
        for cv in CharacterValue.query.filter_by(character_id=char.id).all():
            if cv.raw_value is not None:
                # Ensure raw_value is the acute angle (≤ 90°)
                acute = min(cv.raw_value, 180.0 - cv.raw_value)
                if abs(acute - cv.raw_value) > 0.01:
                    cv.raw_value = acute
                expected_state, expected_conf = map_value_to_state(cv.raw_value, new_states)
                if cv.state != expected_state:
                    cv.state = expected_state
                    cv.confidence = expected_conf
                    remapped += 1
        if remapped:
            print(f'[migrate] A02 project {char.project_id}: remapped {remapped} value(s) to new thresholds.')
            any_changed = True
    if any_changed:
        db.session.commit()


def _migrate_character_states():
    """Update state definitions for A09, C12, C06, A01, C10 and remap existing values."""
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(db.engine)
    if 'character_definitions' not in inspector.get_table_names():
        return

    from app.models import CharacterDefinition, CharacterValue
    from app.characters import map_value_to_state

    updates = {
        'A09': [
            {'code': '0', 'name': 'nearly aligned',
             'description': 'Root nearly continuous with shaft (<25°)',
             'threshold_min': None, 'threshold_max': 25},
            {'code': '1', 'name': 'moderately divergent',
             'description': 'Moderate deviation (25°–60°)',
             'threshold_min': 25, 'threshold_max': 60},
            {'code': '2', 'name': 'widely divergent',
             'description': 'Root departs sharply from shaft (>60°)',
             'threshold_min': 60, 'threshold_max': None},
        ],
        'C12': [
            {'code': '0', 'name': 'abrupt',
             'description': 'Sharp angle at heel-shaft transition (<15°)',
             'threshold_min': None, 'threshold_max': 15},
            {'code': '1', 'name': 'moderate',
             'description': 'Moderate transition angle (15°–40°)',
             'threshold_min': 15, 'threshold_max': 40},
            {'code': '2', 'name': 'gradual',
             'description': 'Smooth, gradual transition (>40°)',
             'threshold_min': 40, 'threshold_max': None},
        ],
        'C06': [
            {'code': '0', 'name': 'near-perpendicular',
             'description': 'Shaft nearly perpendicular to base (<60°)',
             'threshold_min': None, 'threshold_max': 60},
            {'code': '1', 'name': 'moderately angled',
             'description': 'Shaft at moderate angle to base (60°–100°)',
             'threshold_min': 60, 'threshold_max': 100},
            {'code': '2', 'name': 'obtusely angled',
             'description': 'Shaft obtusely angled relative to base (100°–140°)',
             'threshold_min': 100, 'threshold_max': 140},
            {'code': '3', 'name': 'strongly divergent',
             'description': 'Shaft strongly divergent from base (>140°)',
             'threshold_min': 140, 'threshold_max': None},
        ],
        'A01': [
            {'code': '0', 'name': 'much shorter than shaft',
             'description': 'Point much shorter than shaft (<0.45)',
             'threshold_min': None, 'threshold_max': 0.45},
            {'code': '1', 'name': 'approximately half the shaft',
             'description': 'Point approximately half shaft length (0.45–0.55)',
             'threshold_min': 0.45, 'threshold_max': 0.55},
            {'code': '2', 'name': 'shorter than shaft',
             'description': 'Point shorter than shaft (0.55–1.0)',
             'threshold_min': 0.55, 'threshold_max': 1.0},
            {'code': '3', 'name': 'longer than shaft',
             'description': 'Point as long as or longer than shaft (>1.0)',
             'threshold_min': 1.0, 'threshold_max': None},
        ],
        'C10': [
            {'code': '0', 'name': 'reduced',
             'description': 'Heel barely discernible or absent (<0.08)',
             'threshold_min': None, 'threshold_max': 0.08},
            {'code': '1', 'name': 'moderate',
             'description': 'Heel clearly present, moderate size (0.08–0.18)',
             'threshold_min': 0.08, 'threshold_max': 0.18},
            {'code': '2', 'name': 'prominent',
             'description': 'Heel large and conspicuous (>0.18)',
             'threshold_min': 0.18, 'threshold_max': None},
        ],
    }

    any_changed = False
    for code, new_states in updates.items():
        for char in CharacterDefinition.query.filter_by(code=code).all():
            char.states_json = new_states
            remapped = 0
            for cv in CharacterValue.query.filter_by(character_id=char.id).all():
                if cv.raw_value is not None:
                    expected_state, expected_conf = map_value_to_state(cv.raw_value, new_states)
                    if cv.state != expected_state:
                        cv.state = expected_state
                        cv.confidence = expected_conf
                        remapped += 1
            if remapped:
                print(f'[migrate] {code} project {char.project_id}: remapped {remapped} value(s).')
                any_changed = True
    if any_changed:
        db.session.commit()


def _migrate_a06_names():
    """Assign correct biological names to A06 states based on their threshold sign.

    The signed sinuosity is positive when the inner edge bows inward (toward Point)
    and negative when it bows outward.  Whatever threshold values are in place,
    the correct name is determined by whether the state covers negative values
    (outward), spans zero (straight), or covers positive values (inward).

    This also fixes states that were left with placeholder names such as
    'state 0 (edit name)' after the Jenks suggestion tool was used.
    """
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(db.engine)
    if 'character_definitions' not in inspector.get_table_names():
        return

    from app.models import CharacterDefinition

    _DESC = {
        'curved outward': 'Inner edge bows away from the Point (lateral/outward)',
        'straight':       'Inner edge nearly follows basal direction; no clear bow',
        'curved inward':  'Inner edge bows toward the Point (medial/inward)',
    }
    KNOWN_NAMES = set(_DESC.keys())

    def _bio_name(state):
        """Return (name, description) based on threshold sign."""
        t_min = state.get('threshold_min')
        t_max = state.get('threshold_max')
        # Entirely negative range → outward
        if t_max is not None and t_max <= 0 and (t_min is None or t_min < 0):
            k = 'curved outward'
        # Entirely positive range → inward
        elif t_min is not None and t_min >= 0 and (t_max is None or t_max > 0):
            k = 'curved inward'
        else:
            k = 'straight'
        return k, _DESC[k]

    from sqlalchemy.orm.attributes import flag_modified
    import copy

    any_changed = False
    for char in CharacterDefinition.query.filter_by(code='A06').all():
        states = copy.deepcopy(char.states_json or [])
        if not states:
            continue
        changed = False
        for s in states:
            if s.get('name') not in KNOWN_NAMES:
                new_name, new_desc = _bio_name(s)
                s['name'] = new_name
                s['description'] = new_desc
                changed = True
        if changed:
            char.states_json = states
            flag_modified(char, 'states_json')
            any_changed = True
            print(f'[migrate] A06 project {char.project_id}: corrected state names by threshold sign.')
    if any_changed:
        db.session.commit()


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
        ('phylo_method',         'VARCHAR(20)'),
        ('best_fit_model',       'VARCHAR(100)'),
        ('galaxy_api_key',       'VARCHAR(500)'),
        ('restrict_species',     'TEXT'),
        ('partition_spec',       'TEXT'),
        ('partition_presence',   'TEXT'),
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


def _migrate_structures():
    """Add new columns to structures table without dropping existing data."""
    from sqlalchemy import text, inspect as sa_inspect
    inspector = sa_inspect(db.engine)
    if 'structures' not in inspector.get_table_names():
        return
    existing = {c['name'] for c in inspector.get_columns('structures')}
    new_cols = [
        ('no_image', 'BOOLEAN'),
    ]
    with db.engine.connect() as conn:
        for col, col_type in new_cols:
            if col not in existing:
                conn.execute(text(f'ALTER TABLE structures ADD COLUMN {col} {col_type} DEFAULT 0'))
        conn.commit()


def _migrate_specimens():
    """Add new columns to specimens table without dropping existing data."""
    from sqlalchemy import text, inspect as sa_inspect
    inspector = sa_inspect(db.engine)
    if 'specimens' not in inspector.get_table_names():
        return
    existing = {c['name'] for c in inspector.get_columns('specimens')}
    with db.engine.connect() as conn:
        if 'synonyms' not in existing:
            conn.execute(text("ALTER TABLE specimens ADD COLUMN synonyms TEXT DEFAULT '[]'"))
        conn.commit()


def _migrate_a06_states():
    """Update A06 state and formula definitions to the new inner-edge convention.

    Only the state *names* and formula text change; the threshold values (±1.03)
    and state codes are identical, so existing raw_values and state assignments are
    preserved.  A full batch-recompute will update the values to the new algorithm.
    """
    from sqlalchemy import inspect as sa_inspect
    inspector = sa_inspect(db.engine)
    if 'character_definitions' not in inspector.get_table_names():
        return

    new_states = [
        {'code': '0', 'name': 'straight',
         'description': 'Inner edge nearly follows basal direction; no clear bow',
         'threshold_min': -1.03, 'threshold_max': 1.03},
        {'code': '1', 'name': 'curved outward',
         'description': 'Inner edge bows away from the Point (lateral/outward)',
         'threshold_min': None, 'threshold_max': -1.03},
        {'code': '2', 'name': 'curved inward',
         'description': 'Inner edge bows toward the Point (medial/inward)',
         'threshold_min': 1.03, 'threshold_max': None},
    ]
    new_formula = ('arc_length / chord_length × sign; '
                   'sign = +1 when midpoint bows toward Point (inward), '
                   '−1 when bowing away from Point (outward)')

    from app.models import CharacterDefinition
    any_changed = False
    for char in CharacterDefinition.query.filter_by(code='A06').all():
        old_states = char.states_json or []
        old_names = {s.get('code'): s.get('name') for s in old_states}
        # Only migrate if the OLD wrong ordering is detected:
        # old '1' was 'curved inward' (now it should be 'curved outward')
        # or old '2' was 'curved outward' (now it should be 'curved inward')
        if old_names.get('1') == 'curved inward' or \
                old_names.get('2') == 'curved outward':
            char.states_json = new_states
            char.formula = new_formula
            any_changed = True
    if any_changed:
        db.session.commit()
