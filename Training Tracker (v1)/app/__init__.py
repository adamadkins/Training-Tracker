from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate  # 1. Import Migrate
from config import Config

# 2. Initialize extensions
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
migrate = Migrate()  # 2. Initialize Migrate instance


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # 3. Init database, login manager, and migrate
    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)  # 3. Bind Migrate to app and db

    # 4. Register Blueprints
    from app.routes.auth import auth_bp
    from app.routes.manager import manager_bp
    from app.routes.employee import employee_bp
    from app.routes.messages import messages_bp  # Import the new messages blueprint

    app.register_blueprint(auth_bp)
    app.register_blueprint(manager_bp, url_prefix='/manager')
    app.register_blueprint(employee_bp, url_prefix='/employee')
    app.register_blueprint(messages_bp)  # Register the messages blueprint

    # 5. Define the User Loader
    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # 6. Create tables if they don't exist
    # (Note: With Flask-Migrate, this is less critical, but good for safety in dev)
    with app.app_context():
        db.create_all()
        print("Database tables verified/created.")

    return app