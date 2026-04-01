import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'


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

    app.register_blueprint(auth_bp)
    app.register_blueprint(project_bp)
    app.register_blueprint(landmarks_bp)
    app.register_blueprint(boundaries_bp)
    app.register_blueprint(characters_bp)
    app.register_blueprint(matrix_bp)
    app.register_blueprint(descriptions_bp)
    app.register_blueprint(export_bp)

    with app.app_context():
        db.create_all()

    return app
