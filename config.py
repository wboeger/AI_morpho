import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Persistent data location. On Railway, attach a Volume and the platform sets
# RAILWAY_VOLUME_MOUNT_PATH automatically; locally it falls back to ./data.
# Override explicitly with DATA_DIR if needed.
DATA_DIR = (os.environ.get('DATA_DIR')
            or os.environ.get('RAILWAY_VOLUME_MOUNT_PATH')
            or os.path.join(BASE_DIR, 'data'))
os.makedirs(DATA_DIR, exist_ok=True)


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')
    DATA_DIR = DATA_DIR
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(DATA_DIR, 'db.sqlite')}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'timeout': 30}}
    UPLOAD_FOLDER = os.path.join(DATA_DIR, 'uploads')
    DOCS_FOLDER = os.path.join(DATA_DIR, 'docs')  # user manuals on the volume
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50 MB max upload
    UNET_WEIGHTS_DIR = os.path.join(BASE_DIR, 'unet', 'weights')

    # Structure part definitions (fixed terminology)
    STRUCTURE_PARTS = {
        'hook': ['Point', 'Shaft', 'Toe', 'Shelf', 'Base', 'Heel'],
        'anchor': ['Point', 'Shaft', 'SuperficialRoot', 'DeepRoot'],
        'superficial_bar': ['BarProper', 'Shield', 'ShieldDistalEnd', 'AnterolateralProcesses'],
        'deep_bar': [],  # single unit
        'mco': ['Bulb', 'PrincipalSpine', 'Spinelets'],
    }

    # Fixed landmark counts
    LANDMARK_COUNTS = {
        'hook': 100,
        'anchor': 100,
        'superficial_bar': None,   # adaptive 60-120
        'deep_bar': None,          # adaptive 60-120
        'mco': None,               # adaptive 80-150
    }

    ADAPTIVE_RANGES = {
        'superficial_bar': (60, 120),
        'deep_bar': (60, 120),
        'mco': (80, 150),
    }

    # Galaxy phylogenetic analysis (usegalaxy.eu)
    GALAXY_BASE_URL = os.environ.get('GALAXY_BASE_URL', 'https://usegalaxy.eu')
    GALAXY_API_KEY  = os.environ.get('GALAXY_API_KEY', '')
    # Tool IDs — override via env if usegalaxy.eu updates versions
    GALAXY_RAXML_TOOL_ID   = os.environ.get(
        'GALAXY_RAXML_TOOL_ID',
        'toolshed.g2.bx.psu.edu/repos/iuc/raxml/raxml/8.2.12+galaxy2')
    GALAXY_MRBAYES_TOOL_ID = os.environ.get(
        'GALAXY_MRBAYES_TOOL_ID',
        'toolshed.g2.bx.psu.edu/repos/iuc/mrbayes/mrbayes/3.2.7.a+galaxy0')
    # MrBayes runs locally (no public Galaxy tool returns the tree files).
    MRBAYES_BIN     = os.environ.get('MRBAYES_BIN', '')          # '' → search PATH for mb
    MRBAYES_TIMEOUT = int(os.environ.get('MRBAYES_TIMEOUT', str(6 * 3600)))
    # MAFFT alignment + trimAl trimming on Galaxy (so the server needs no local
    # bioinformatics binaries). Tool IDs / input keys / extra params are all
    # env-overridable in case usegalaxy.eu updates tool versions.
    GALAXY_MAFFT_TOOL_ID = os.environ.get(
        'GALAXY_MAFFT_TOOL_ID',
        'toolshed.g2.bx.psu.edu/repos/rnateam/mafft/rbc_mafft/7.526+galaxy1')
    GALAXY_MAFFT_INPUT_KEY = os.environ.get('GALAXY_MAFFT_INPUT_KEY', 'inputSequences')
    GALAXY_MAFFT_PARAMS = os.environ.get(
        'GALAXY_MAFFT_PARAMS', '{"cond_flavour|flavourType": "mafft --auto"}')
    GALAXY_TRIMAL_TOOL_ID = os.environ.get(
        'GALAXY_TRIMAL_TOOL_ID',
        'toolshed.g2.bx.psu.edu/repos/iuc/trimal/trimal/1.4.1')
    GALAXY_TRIMAL_INPUT_KEY = os.environ.get('GALAXY_TRIMAL_INPUT_KEY', 'in')
    GALAXY_TRIMAL_PARAMS = os.environ.get(
        'GALAXY_TRIMAL_PARAMS', '{"trimming_mode|mode": "gappyout"}')
    # Force Galaxy even when local mafft/trimal exist (default on in production)
    PHYLO_FORCE_GALAXY = os.environ.get('PHYLO_FORCE_GALAXY', '0') in ('1', 'true', 'True')
