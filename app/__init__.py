from datetime import datetime, timezone

from flask import Flask, session, redirect, url_for, flash, request

APP_VERSION = "2.3.0"
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

    # Trust reverse-proxy headers (Nginx sets X-Forwarded-Proto and X-Forwarded-Host).
    # This ensures url_for(_external=True) produces https://trainingtracker.me/...
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    cache.init_app(app)

    # 4. Register Blueprints
    from app.routes.auth import auth_bp
    from app.routes.manager import manager_bp
    from app.routes.employee import employee_bp
    from app.routes.messages import messages_bp
    from app.routes.legacy import legacy_bp
    from app.routes.guest import guest_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(manager_bp, url_prefix='/manager')
    app.register_blueprint(employee_bp, url_prefix='/employee')
    app.register_blueprint(messages_bp)
    app.register_blueprint(legacy_bp)
    app.register_blueprint(guest_bp)

    # 5. Inject app version and system_settings into all templates
    @app.before_request
    def inject_system_settings():
        from flask import g
        from app.models import SystemSettings
        from app import cache
        try:
            g.system_settings = cache.get('system_settings')
        except Exception:
            g.system_settings = None
        if g.system_settings is None:
            g.system_settings = SystemSettings.query.first()
            try:
                cache.set('system_settings', g.system_settings, timeout=60)
            except Exception:
                pass

    @app.context_processor
    def inject_version():
        return {'app_version': APP_VERSION}

    # Error handlers
    from flask import render_template as _render

    @app.errorhandler(403)
    def forbidden(e):
        return _render('error.html',
            code=403, icon='🔒',
            title="Access Denied",
            message="You don't have permission to view this page. If you think this is a mistake, contact your manager."), 403

    @app.errorhandler(404)
    def not_found(e):
        return _render('error.html',
            code=404, icon='🔍',
            title="Page Not Found",
            message="This page doesn't exist or the link may have expired. Double-check the URL or head back home."), 404

    @app.errorhandler(410)
    def gone(e):
        return _render('error.html',
            code=410, icon='⏰',
            title="Link Expired",
            message="This link is no longer valid. It may have already been used or it expired. Ask your manager to generate a new one."), 410

    @app.errorhandler(500)
    def server_error(e):
        return _render('error.html',
            code=500, icon='⚠️',
            title="Something Went Wrong",
            message="An unexpected error occurred on our end. Try refreshing the page or come back in a moment."), 500

    @app.errorhandler(401)
    def unauthorized(e):
        return _render('error.html',
            code=401, icon='🔑',
            title="Login Required",
            message="You need to be logged in to view this page."), 401

    # 7. Define the User Loader
    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # 8. Session idle-timeout enforcement
    @app.before_request
    def enforce_session_timeout():
        # Skip static files and the auth endpoints to avoid redirect loops
        if request.endpoint and (
            request.endpoint.startswith('static') or
            request.endpoint.startswith('guest.') or
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