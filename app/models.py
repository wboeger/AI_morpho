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
    active = db.Column(db.Boolean, default=True)  # False = login disabled
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
    tree_fragments = db.Column(db.JSON)  # {normalized_species: '18S+ITS'|'18S'|
                                         # 'ITS'} carried from the imported job,
                                         # for coloring tree tips by DNA fragment

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
    synonyms = db.Column(db.JSON, default=list)  # list of synonym strings
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    creator = db.relationship('User')
    structures = db.relationship('Structure', backref='specimen', cascade='all, delete-orphan')
    dna_sequences = db.relationship('DNASequence', backref='specimen', cascade='all, delete-orphan')
    comments = db.relationship('SpecimenComment', backref='specimen',
                               cascade='all, delete-orphan',
                               order_by='SpecimenComment.created_at')


class SpecimenComment(db.Model):
    __tablename__ = 'specimen_comments'
    id = db.Column(db.Integer, primary_key=True)
    specimen_id = db.Column(db.Integer, db.ForeignKey('specimens.id'), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    author = db.relationship('User')


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
    no_image = db.Column(db.Boolean, default=False)  # explicitly marked as no image available
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
    history_json  = db.Column(db.JSON, default=list)
    display_order = db.Column(db.Integer)   # custom sort position; NULL = fall back to code

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


class SpeciesAlias(db.Model):
    """Maps a normalized tree tip label to an exact specimen species_name."""
    __tablename__ = 'species_aliases'
    id           = db.Column(db.Integer, primary_key=True)
    project_id   = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    tree_label   = db.Column(db.String(300), nullable=False)   # normalized (lowercase, spaces)
    specimen_name = db.Column(db.String(300), nullable=False)  # exact Specimen.species_name
    created_by   = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at   = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    creator = db.relationship('User')
    project = db.relationship('Project', backref='species_aliases')
    __table_args__ = (db.UniqueConstraint('project_id', 'tree_label'),)


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

    # Galaxy (usegalaxy.eu) credential
    galaxy_api_key = db.Column(db.String(500))

    # Legacy CIPRES fields — kept so old job records render without errors
    cipres_user = db.Column(db.String(100))
    cipres_password_enc = db.Column(db.String(500))
    cipres_app_key = db.Column(db.String(200))

    # Job tracking (Galaxy: job_handle = Galaxy job ID, job_url = Galaxy history ID)
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
    max_length_factor = db.Column(db.Float, default=2.0)
    trim_mode = db.Column(db.String(20), default='gappyout')  # trimAl behaviour:
                                            # 'gappyout' (standard, no-loss fallback),
                                            # 'automated1' (gentler, no-loss fallback),
                                            # or 'none' (skip trimAl — keep full
                                            # alignment, never drop a column/sequence)
    nj_newick = db.Column(db.Text)                       # rapid NJ tree Newick
    outgroup_definitions = db.Column(db.JSON)   # [{family, mode, n}, ...]
    bad_accessions = db.Column(db.JSON, default=list)
    restrict_species = db.Column(db.JSON)   # optional list of species names; if set,
                                            # ingroup is limited to these (from Specimens page)
    partition_spec = db.Column(db.JSON)     # [{name,start,end}] column ranges per
                                            # fragment for partitioned model selection
    partition_presence = db.Column(db.JSON)  # {normalized_species: '18S+ITS'|'18S'|
                                            # 'ITS'} — which markers each taxon had
                                            # in the concatenation (for tip coloring)
    flipped_sequences = db.Column(db.JSON, default=list)   # ids reverse-complemented
                                            # by MAFFT --adjustdirection / Galaxy
                                            # orientation heuristic
    missing_specimens = db.Column(db.JSON, default=list)   # Specimens-page species
                                            # names with no sequence in the final
                                            # alignment
    low_quality_sequences = db.Column(db.JSON, default=list)  # [{id, reason}, ...]
                                            # flagged for excessive ambiguous bases
    pending_candidates = db.Column(db.JSON, default=dict)  # {marker_suffix:
                                            # {norm_species: {display, candidates:
                                            # [{accession,length,description}]}}}
                                            # — flexible-search hits awaiting
                                            # explicit user accept/reject before
                                            # the pipeline proceeds to alignment

    # Sequence counts through pipeline
    n_sequences_raw = db.Column(db.Integer)
    n_sequences_deduped = db.Column(db.Integer)
    n_sequences_final = db.Column(db.Integer)

    # File paths within result_dir
    raw_fasta_path = db.Column(db.String(500))
    aligned_fasta_path = db.Column(db.String(500))
    trimmed_fasta_path = db.Column(db.String(500))

    # Inference method label ('raxml')
    phylo_method = db.Column(db.String(20), default='raxml')

    # Model selection (ModelTest-NG)
    best_fit_model = db.Column(db.String(100))   # e.g. "GTR+I+G4"

    project = db.relationship('Project', backref='phylogeny_jobs')
    submitter = db.relationship('User', foreign_keys=[submitted_by])
