from datetime import datetime, timezone
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app import db


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default='annotator')  # admin, annotator, reviewer
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Project(db.Model):
    __tablename__ = 'projects'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    tree_newick = db.Column(db.Text)  # reference phylogeny

    creator = db.relationship('User', backref='owned_projects')
    specimens = db.relationship('Specimen', backref='project', cascade='all, delete-orphan')
    characters = db.relationship('CharacterDefinition', backref='project', cascade='all, delete-orphan')
    taxonomic_groups = db.relationship('TaxonomicGroup', backref='project', cascade='all, delete-orphan')


class ProjectMembership(db.Model):
    __tablename__ = 'project_memberships'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    role = db.Column(db.String(20), default='annotator')

    user = db.relationship('User', backref='memberships')
    project = db.relationship('Project', backref='memberships')

    __table_args__ = (db.UniqueConstraint('user_id', 'project_id'),)


class Specimen(db.Model):
    __tablename__ = 'specimens'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    species_name = db.Column(db.String(200), nullable=False)
    specimen_id_label = db.Column(db.String(200))
    image_path = db.Column(db.String(500))
    notes = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    creator = db.relationship('User')
    structures = db.relationship('Structure', backref='specimen', cascade='all, delete-orphan')
    dna_sequences = db.relationship('DNASequence', backref='specimen', cascade='all, delete-orphan')


class DNASequence(db.Model):
    __tablename__ = 'dna_sequences'
    id = db.Column(db.Integer, primary_key=True)
    specimen_id = db.Column(db.Integer, db.ForeignKey('specimens.id'), nullable=False)
    marker = db.Column(db.String(50), nullable=False)  # ITS, 18S, COI, other
    accession = db.Column(db.String(100))
    available = db.Column(db.Boolean, default=True)


class Structure(db.Model):
    __tablename__ = 'structures'
    id = db.Column(db.Integer, primary_key=True)
    specimen_id = db.Column(db.Integer, db.ForeignKey('specimens.id'), nullable=False)
    structure_type = db.Column(db.String(30), nullable=False)  # hook, anchor, superficial_bar, deep_bar, mco
    image_path = db.Column(db.String(500))
    landmarks_json = db.Column(db.JSON)       # [[x,y], [x,y], ...]
    landmarks_confirmed = db.Column(db.Boolean, default=False)
    boundary_json = db.Column(db.JSON)        # {"Part": [indices], ...}
    boundary_confirmed = db.Column(db.Boolean, default=False)
    landmark_count = db.Column(db.Integer)    # actual count (100 for hook/anchor, adaptive for others)

    character_values = db.relationship('CharacterValue', backref='structure', cascade='all, delete-orphan')


class CharacterDefinition(db.Model):
    __tablename__ = 'character_definitions'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    code = db.Column(db.String(20), nullable=False)  # C01, B03, M_NEW_01
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    structure_type = db.Column(db.String(30), nullable=False)
    computation_type = db.Column(db.String(20), nullable=False)  # geometric, manual
    parts_involved = db.Column(db.JSON)        # ["Point", "Shaft"]
    geometric_operation = db.Column(db.String(50))
    formula = db.Column(db.String(500))
    states_json = db.Column(db.JSON)           # [{code, name, description, threshold_min, threshold_max}]
    dependencies_json = db.Column(db.JSON)     # [{if_character, if_state, then}]
    active = db.Column(db.Boolean, default=True)
    exemplar_images = db.Column(db.JSON)       # {state_code: image_path}
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    modified_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc))
    history_json = db.Column(db.JSON, default=list)

    creator = db.relationship('User')
    values = db.relationship('CharacterValue', backref='character', cascade='all, delete-orphan')

    __table_args__ = (db.UniqueConstraint('project_id', 'code'),)


class CharacterValue(db.Model):
    __tablename__ = 'character_values'
    id = db.Column(db.Integer, primary_key=True)
    structure_id = db.Column(db.Integer, db.ForeignKey('structures.id'), nullable=False)
    character_id = db.Column(db.Integer, db.ForeignKey('character_definitions.id'), nullable=False)
    raw_value = db.Column(db.Float)
    state = db.Column(db.String(10))  # "0", "1", ... or "-" or "?"
    confidence = db.Column(db.Float)
    auto_assigned = db.Column(db.Boolean, default=False)
    override_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    override_reason = db.Column(db.Text)
    override_at = db.Column(db.DateTime)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('users.id'))  # for consensus mode

    overrider = db.relationship('User', foreign_keys=[override_by])
    reviewer = db.relationship('User', foreign_keys=[reviewer_id])

    __table_args__ = (db.UniqueConstraint('structure_id', 'character_id', 'reviewer_id'),)


class TaxonomicGroup(db.Model):
    __tablename__ = 'taxonomic_groups'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    rank = db.Column(db.String(50))  # genus, subfamily, family
    parent_id = db.Column(db.Integer, db.ForeignKey('taxonomic_groups.id'))
    included_species = db.Column(db.JSON)  # ["species1", "species2"]
    diagnosis_text = db.Column(db.Text)
    diagnosis_generated_at = db.Column(db.DateTime)

    parent = db.relationship('TaxonomicGroup', remote_side=[id], backref='children')


class CorrectionHistory(db.Model):
    __tablename__ = 'correction_history'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    structure_id = db.Column(db.Integer, db.ForeignKey('structures.id'))
    character_id = db.Column(db.Integer, db.ForeignKey('character_definitions.id'))
    old_state = db.Column(db.String(10))
    new_state = db.Column(db.String(10))
    reason = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User')
    structure = db.relationship('Structure')
    character = db.relationship('CharacterDefinition')


class ActivityLog(db.Model):
    __tablename__ = 'activity_log'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    action = db.Column(db.String(200), nullable=False)
    details = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User')


class PhylogenyJob(db.Model):
    __tablename__ = 'phylogeny_jobs'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    submitted_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    submitted_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = db.Column(db.DateTime)
    last_checked = db.Column(db.DateTime)

    # CIPRES credentials stored for polling
    cipres_user = db.Column(db.String(100))
    cipres_password_enc = db.Column(db.String(500))
    cipres_app_key = db.Column(db.String(200))

    # CIPRES job tracking
    job_url = db.Column(db.String(500))
    job_handle = db.Column(db.String(200))
    results_url = db.Column(db.String(500))

    # Core parameters
    fasta_filename = db.Column(db.String(200))
    marker = db.Column(db.String(50), default='18S')
    n_bootstraps = db.Column(db.Integer, default=1000)
    n_sequences = db.Column(db.Integer)
    sequences_removed = db.Column(db.JSON, default=list)
    outgroup_genera = db.Column(db.JSON)  # list of genera for tree rooting

    # Results
    result_dir = db.Column(db.String(500))
    tree_newick = db.Column(db.Text)

    # Pipeline status:
    #   created | fetching | fetched | aligning | aligned | trimming | trimmed
    #   | submitted | running | completed | tree_ready | failed
    status = db.Column(db.String(50), default='created')
    status_message = db.Column(db.Text)

    # --- NCBI retrieval settings ---
    ncbi_email = db.Column(db.String(200))
    target_taxon = db.Column(db.String(200))
    gene_query = db.Column(db.Text)
    min_length = db.Column(db.Integer, default=400)
    outgroup_definitions = db.Column(db.JSON)   # [{family, mode, n}, ...]
    bad_accessions = db.Column(db.JSON, default=list)

    # Sequence counts through pipeline
    n_sequences_raw = db.Column(db.Integer)
    n_sequences_deduped = db.Column(db.Integer)
    n_sequences_final = db.Column(db.Integer)

    # File paths within result_dir
    raw_fasta_path = db.Column(db.String(500))
    aligned_fasta_path = db.Column(db.String(500))
    trimmed_fasta_path = db.Column(db.String(500))

    project = db.relationship('Project', backref='phylogeny_jobs')
    submitter = db.relationship('User', foreign_keys=[submitted_by])
