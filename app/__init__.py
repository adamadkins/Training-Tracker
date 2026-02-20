from datetime import datetime, timezone

from flask import Flask, session, redirect, url_for, flash, request
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, logout_user, current_user
from flask_migrate import Migrate
from flask_caching import Cache
from config import Config

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
migrate = Migrate()
cache = Cache()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    cache.init_app(app)

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

    # 6. Session idle-timeout enforcement
    @app.before_request
    def enforce_session_timeout():
        # Skip static files and the auth endpoints to avoid redirect loops
        if request.endpoint and (
            request.endpoint.startswith('static') or
            request.endpoint in ('auth.login', 'auth.logout',
                                 'auth.forgot_password', 'auth.reset_token')
        ):
            return

        if not current_user.is_authenticated:
            return

        timeout = app.config.get('SESSION_IDLE_TIMEOUT', 7200)
        last_active = session.get('_last_active')
        now_ts = datetime.now(timezone.utc).timestamp()

        if last_active and (now_ts - last_active) > timeout:
            logout_user()
            session.clear()
            flash("Your session expired due to inactivity. Please log in again.", "warning")
            return redirect(url_for('auth.login'))

        session['_last_active'] = now_ts
        session.permanent = True

    return app