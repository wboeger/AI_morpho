import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(BASE_DIR, 'data', 'db.sqlite')}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'timeout': 30}}
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'data', 'uploads')
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
