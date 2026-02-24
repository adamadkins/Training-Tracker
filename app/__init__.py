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
    from app.routes.admin import admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(manager_bp, url_prefix='/manager')
    app.register_blueprint(employee_bp, url_prefix='/employee')
    app.register_blueprint(messages_bp)
    app.register_blueprint(legacy_bp)
    app.register_blueprint(guest_bp)
    app.register_blueprint(admin_bp)

    # Multi-tenant: resolve current org from subdomain so routes and system_settings can use it
    @app.before_request
    def resolve_tenant():
        from flask import g
        g.current_organization_id = None
        try:
            from app.models import Organization
            host = (request.host or "").split(":")[0].lower()
            main = (app.config.get("MAIN_DOMAIN") or "trainingtracker.me").split(":")[0].lower()
            if host == main or host.startswith("www.") or "." not in host:
                return
            parts = host.replace("www.", "").split(".")
            subdomain = parts[0] if len(parts) >= 2 else None
            if not subdomain:
                return
            org = Organization.query.filter_by(subdomain=subdomain).first()
            g.current_organization_id = org.id if org else None
        except Exception:
            app.logger.exception("resolve_tenant failed")
            g.current_organization_id = None

    # 5. Inject app version and system_settings into all templates
    @app.before_request
    def inject_system_settings():
        import hashlib
        from flask import g
        from app.models import SystemSettings
        org_id = getattr(g, "current_organization_id", None)
        if org_id is not None:
            settings = SystemSettings.query.filter_by(organization_id=org_id).first()
        else:
            # Main domain: no tenant, so no tenant-specific settings (admin uses its own UI)
            settings = None
        g.system_settings = settings
        # Stable cache key per logo so img src doesn't flicker on refresh; changes when logo changes
        logo_url = (settings and getattr(settings, "custom_logo_url", None)) or ""
        g.logo_version = hashlib.md5(logo_url.encode()).hexdigest()[:12] if logo_url else ""

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
        import os
        import sys
        import traceback
        tb = None
        if sys.exc_info()[0]:
            tb = traceback.format_exc()
            app.logger.error("500 error:\n%s", tb)
        if os.environ.get("SHOW_500_TRACEBACK"):
            msg = (tb or str(e) or "Unknown error") if tb else "No traceback captured. Check server logs: sudo journalctl -u training-tracker -n 80 --no-pager"
            return _render('error.html',
                code=500, icon='⚠️',
                title="Something Went Wrong",
                message=msg), 500
        return _render('error.html',
            code=500, icon='⚠️',
            title="Something Went Wrong",
            message="An unexpected error occurred. Try again or check server logs: sudo journalctl -u training-tracker -n 80 --no-pager"), 500

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