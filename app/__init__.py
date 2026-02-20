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
    app.register_blueprint(admin_bp)
    app.register_blueprint(manager_bp, url_prefix='/manager')
    app.register_blueprint(employee_bp, url_prefix='/employee')
    app.register_blueprint(messages_bp)
    app.register_blueprint(legacy_bp)
    app.register_blueprint(guest_bp)

    # 5. Resolve tenant from subdomain (before any tenant-scoped logic)
    @app.before_request
    def resolve_tenant():
        from flask import g
        from app.models import Organization
        host = (request.host or "").lower().split(":")[0]
        main_domain = app.config.get("MAIN_DOMAIN", "trainingtracker.me").lower()
        main_domain = main_domain.split(":")[0]
        if host == main_domain or host == "www." + main_domain:
            g.current_organization_id = None
            g.current_organization = None
            return
        # Subdomain: e.g. acme.trainingtracker.me -> subdomain "acme"
        if host.endswith("." + main_domain):
            subdomain = host[: -len("." + main_domain)].strip()
        elif main_domain in host and host.startswith("www."):
            subdomain = None
        else:
            subdomain = host.split(".")[0] if "." in host else host
        if not subdomain or subdomain == "www":
            g.current_organization_id = None
            g.current_organization = None
            return
        org = Organization.query.filter_by(subdomain=subdomain).first()
        if not org:
            from flask import render_template
            return render_template("error.html", code=404, icon="\u26a0\ufe0f",
                title="Organization Not Found",
                message="This subdomain is not registered. Check the URL or contact your administrator."), 404
        if getattr(org, "status", "active") != "active":
            from flask import render_template
            return render_template("error.html", code=403, icon="\u1f6ab",
                title="Service Suspended",
                message="This account has been suspended. Please contact your administrator or billing to restore access."), 403
        g.current_organization_id = org.id
        g.current_organization = org

    # 6. Inject app version and system_settings into all templates (tenant-scoped)
    @app.before_request
    def inject_system_settings():
        import hashlib
        from flask import g
        from app.models import SystemSettings
        if getattr(g, "current_organization_id", None) is None:
            g.system_settings = None
            g.logo_version = ""
            return
        settings = SystemSettings.query.filter_by(organization_id=g.current_organization_id).first()
        if not settings:
            settings = SystemSettings(organization_id=g.current_organization_id)
            db.session.add(settings)
            db.session.commit()
        g.system_settings = settings
        logo_url = (settings and settings.custom_logo_url) or ""
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

    # CLI: create platform superuser (main domain admin)
    @app.cli.command("create-superuser")
    def create_superuser_cmd():
        """Create a platform admin user (superuser). Log in at main domain (e.g. trainingtracker.me/login)."""
        import getpass
        from app.models import User
        email = input("Email: ").strip().lower()
        if not email:
            print("Email required.")
            return
        existing_super = User.query.filter_by(organization_id=None, email=email).first()
        if existing_super:
            print("A superuser with that email already exists.")
            return
        password = getpass.getpass("Password: ")
        if len(password) < 8:
            print("Password must be at least 8 characters.")
            return
        password2 = getpass.getpass("Confirm password: ")
        if password != password2:
            print("Passwords do not match.")
            return
        # If email exists in an org, promote that user to superuser instead of creating a new row
        existing_org = User.query.filter(User.email == email).filter(User.organization_id.isnot(None)).first()
        if existing_org:
            existing_org.organization_id = None
            existing_org.is_superuser = True
            existing_org.employee_id = None
            existing_org.set_password(password)
            db.session.commit()
            print("Existing user promoted to superuser. Log in at the main domain (e.g. trainingtracker.me/login).")
            return
        user = User(email=email, organization_id=None, is_superuser=True, role="manager")
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print("Superuser created. Log in at the main domain (e.g. trainingtracker.me/login).")

    return app