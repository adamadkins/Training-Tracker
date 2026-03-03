import os
import uuid
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, current_app, send_file, Response, abort, g
from flask_login import login_user, logout_user, login_required, current_user
from app.models import User, SystemSettings, Organization, SignupRequest
from app import db
# ADDED: Import your notification helper
from app.utils.notifications import send_notification_email

auth_bp = Blueprint("auth", __name__)

# Browser-like User-Agent so logo CDNs (e.g. Clearbit) are more likely to allow the request
_LOGO_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; rv:91.0) Gecko/20100101 Firefox/91.0"


def _fetch_logo_image(url):
    """Fetch image bytes from URL; returns (data, content_type) or (None, None)."""
    if not url or not url.startswith("http"):
        return None, None
    for ua in (_LOGO_USER_AGENT, "TrainingTracker/1.0"):
        try:
            req = Request(url, headers={"User-Agent": ua})
            with urlopen(req, timeout=10) as r:
                data = r.read()
                ctype = (r.headers.get("Content-Type") or "image/png").split(";")[0].strip()
                if data and len(data) <= 2 * 1024 * 1024:
                    return data, ctype
        except (HTTPError, URLError, OSError):
            continue
    return None, None


def _download_logo_to_uploads(url):
    """Download image from URL and save to static/uploads/logos/. Returns relative path or None."""
    if not url or not url.startswith("http"):
        return None
    try:
        req = Request(url, headers={"User-Agent": _LOGO_USER_AGENT})
        with urlopen(req, timeout=10) as r:
            data = r.read()
            ctype = (r.headers.get("Content-Type") or "").lower()
        if not data or len(data) > 5 * 1024 * 1024:
            return None
        ext = ".png"
        if "jpeg" in ctype or "jpg" in ctype:
            ext = ".jpg"
        elif "gif" in ctype:
            ext = ".gif"
        elif "webp" in ctype:
            ext = ".webp"
        static_folder = current_app.static_folder
        if not static_folder:
            return None
        upload_dir = os.path.join(static_folder, "uploads", "logos")
        os.makedirs(upload_dir, exist_ok=True)
        name = str(uuid.uuid4()) + ext
        path = os.path.join(upload_dir, name)
        with open(path, "wb") as f:
            f.write(data)
        return "uploads/logos/" + name
    except Exception:
        return None


def _logo_google_fallback_url(primary_url):
    """If primary is a Clearbit logo URL, return Google favicon URL for the same domain."""
    if not primary_url or "logo.clearbit.com" not in primary_url:
        return None
    try:
        # e.g. https://logo.clearbit.com/domain.com -> domain.com
        domain = primary_url.split("logo.clearbit.com/")[-1].split("?")[0].strip()
        if domain and "." in domain:
            return f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
    except Exception:
        pass
    return None


# 1x1 transparent PNG so /logo never 404s when a logo is configured (avoids broken img when file missing or fetch fails)
_EMPTY_LOGO_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

_NO_CACHE_HEADERS = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
# Allow caching real logo (img uses ?v= per logo so new logo = new URL); avoids flicker on refresh
_LOGO_CACHE_HEADERS = {"Cache-Control": "public, max-age=3600"}


@auth_bp.get("/logo")
def logo():
    """Serve the configured system logo (no auth). Used by navbar and login page. Resolved from current tenant (g.system_settings)."""
    settings = getattr(g, "system_settings", None)
    if not settings or not getattr(settings, "custom_logo_url", None):
        abort(404)
    logo_url = (settings.custom_logo_url or "").strip()
    if not logo_url:
        abort(404)
    if logo_url.startswith("http"):
        data, ctype = _fetch_logo_image(logo_url)
        if data and ctype:
            r = Response(data, mimetype=ctype, direct_passthrough=True)
            r.headers.update(_LOGO_CACHE_HEADERS)
            return r
        # Fetch failed (e.g. CDN blocks server). Try once to download and persist to uploads, then serve from disk.
        local_path = _download_logo_to_uploads(logo_url)
        if not local_path:
            fallback_url = _logo_google_fallback_url(logo_url)
            if fallback_url:
                local_path = _download_logo_to_uploads(fallback_url)
        if local_path:
            settings.custom_logo_url = local_path
            db.session.commit()
            path = os.path.join(current_app.static_folder, local_path)
            ext = os.path.splitext(local_path)[1].lower()
            mimetype = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "image/png")
            r = send_file(path, mimetype=mimetype, as_attachment=False)
            r.headers.update(_LOGO_CACHE_HEADERS)
            return r
        r = Response(_EMPTY_LOGO_PNG, mimetype="image/png")
        r.headers.update(_NO_CACHE_HEADERS)
        return r
    # Serve local logo via static URL so path resolution matches Flask/nginx exactly
    static_folder = current_app.static_folder
    if not static_folder:
        r = Response(_EMPTY_LOGO_PNG, mimetype="image/png")
        r.headers.update(_NO_CACHE_HEADERS)
        return r
    path = os.path.normpath(os.path.join(static_folder, logo_url))
    try:
        path = os.path.realpath(path)
        static_root = os.path.realpath(static_folder)
        if os.path.commonpath([path, static_root]) != static_root or not os.path.isfile(path):
            r = Response(_EMPTY_LOGO_PNG, mimetype="image/png")
            r.headers.update(_NO_CACHE_HEADERS)
            return r
    except (ValueError, OSError):
        r = Response(_EMPTY_LOGO_PNG, mimetype="image/png")
        r.headers.update(_NO_CACHE_HEADERS)
        return r
    r = redirect(url_for("static", filename=logo_url))
    r.headers.update(_LOGO_CACHE_HEADERS)
    return r


@auth_bp.get("/")
def home():
    if current_user.is_authenticated:
        if getattr(current_user, "is_superuser", False) and getattr(g, "current_organization_id", None) is None:
            return redirect(url_for("admin.index"))
        if current_user.role == "manager":
            return redirect(url_for("manager.dashboard"))
        return redirect(url_for("employee.dashboard"))
    return render_template("landing.html")


@auth_bp.get("/tour")
def tour():
    return render_template("tour.html")


@auth_bp.get("/terms")
def terms():
    return render_template("terms.html")


@auth_bp.get("/privacy")
def privacy():
    return render_template("privacy.html")


@auth_bp.get("/cookies")
def cookies():
    return render_template("cookies.html")


SUPPORT_EMAIL = "support@trainingtracker.me"


@auth_bp.route("/support", methods=["GET", "POST"])
def support():
    """Support page and contact form; submissions saved to DB and emailed to SUPPORT_EMAIL."""
    if request.method == "POST":
        from app.models import SupportRequest
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        subject = (request.form.get("subject") or "Support request").strip() or "Support request"
        message = (request.form.get("message") or "").strip()
        if not email:
            flash("Please enter your email address.", "error")
            return redirect(url_for("auth.support"))
        try:
            req = SupportRequest(name=name or None, email=email, subject=subject, message=message or None)
            db.session.add(req)
            db.session.commit()
            from app.utils.notifications import _enqueue_or_send_email
            body = (
                "Support / Contact form\n\n"
                f"From: {name or '(not provided)'} <{email}>\n"
                f"Subject: {subject}\n\n"
                f"{message if message else '(no message)'}"
            )
            _enqueue_or_send_email(SUPPORT_EMAIL, f"[Training Tracker] {subject}", body)
            flash("Thanks! Your message has been sent. We'll get back to you soon.", "success")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Support form send failed")
            flash("Something went wrong sending your message. Please try again or email us directly.", "error")
        return redirect(url_for("auth.support"))
    return render_template("support.html")


@auth_bp.route("/app-coming-soon")
def app_coming_soon():
    """Mobile app coming soon page; optional ?redirect= URL to continue in browser."""
    redirect_url = request.args.get("redirect", "").strip()
    if not redirect_url or not (redirect_url.startswith("https://") or redirect_url.startswith("http://")):
        redirect_url = None
    return render_template("app_coming_soon.html", redirect_url=redirect_url)


@auth_bp.route("/open-in-app")
def open_in_app():
    """Prompt to open the Training Tracker app (for invite/set-password links), then go to their company."""
    from urllib.parse import quote
    redirect_url = request.args.get("redirect", "").strip()
    if not redirect_url or not (redirect_url.startswith("https://") or redirect_url.startswith("http://")):
        flash("Invalid link. Please use the link from your invitation email.", "error")
        return redirect(url_for("auth.home"))
    # Deep link for the native app: trainingtracker://open?url=ENCODED_URL
    app_url = "trainingtracker://open?url=" + quote(redirect_url, safe="")
    coming_soon_url = url_for("auth.app_coming_soon", redirect=redirect_url) if redirect_url else url_for("auth.app_coming_soon")
    return render_template("open_in_app.html", redirect_url=redirect_url, app_url=app_url, coming_soon_url=coming_soon_url)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("auth.home"))

    org_id = getattr(g, "current_organization_id", None)

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if org_id is not None:
            user = User.query.filter_by(organization_id=org_id, email=email).first()
        else:
            # Main domain: only superusers (organization_id is None) can log in
            user = User.query.filter_by(organization_id=None, email=email).first()

        if user and user.password_hash is None:
            flash("Your account is not set up yet. Please use the link sent to your email.")
            return redirect(url_for("auth.login"))

        if not user or not user.check_password(password):
            if org_id is None:
                flash("No platform admin account found. Use your company URL to sign in (e.g. app.yourdomain.com).")
            else:
                flash("Invalid email or password.")
            return render_template("login.html"), 401

        session.permanent = True
        session['_last_active'] = datetime.now(timezone.utc).timestamp()
        login_user(user, remember=True)

        if org_id is None and getattr(user, "is_superuser", False):
            return redirect(url_for("admin.index"))
        if user.role == "manager":
            return redirect(url_for("manager.dashboard"))
        return redirect(url_for("employee.dashboard"))

    # Don't cache login page so app WebView always gets latest (e.g. "Use different company" text)
    if org_id is None:
        # Main domain: show login form for platform admins (no_tenant_login for anonymous would go on landing)
        resp = current_app.make_response(render_template("login.html", admin_login=True))
    else:
        resp = current_app.make_response(render_template("login.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@auth_bp.get("/leave-company")
def leave_company():
    """Minimal page that tells the app shell (when in iframe) to show the company picker."""
    html = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Switch company</title></head><body style="margin:0;font-family:system-ui;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#f1f5f9;color:#1e293b;">
<script>
(function(){
  try { window.parent.postMessage({ type: 'TrainingTrackerShowCompanyPicker' }, '*'); } catch (e) {}
  setTimeout(function() {
      if (window === window.parent) {
          window.location.href = "/";
      }
  }, 100);
})();
</script>
<p style="font-size:15px;color:#64748b;">Returning to company selection&hellip;</p>
</body></html>"""
    return Response(html, mimetype="text/html", headers={"Cache-Control": "no-store"})


# --- New: Forgot Password Request ---

@auth_bp.route("/forgot_password", methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('auth.home'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        org_id = getattr(g, "current_organization_id", None)
        if org_id is not None:
            user = User.query.filter_by(organization_id=org_id, email=email).first()
        else:
            user = User.query.filter_by(email=email).first()

        # We show success regardless of whether the email exists for security
        if user:
            token = user.get_reset_token()
            # Note: Using 'auth.reset_token' to match your function name below
            reset_url = url_for('auth.reset_token', token=token, _external=True)

            title = "Password Reset Request"
            body = f"Click the link below to reset your password:\n\n{reset_url}\n\nIf you didn't request this, ignore this email."

            try:
                send_notification_email(user, title, body)
            except Exception as e:
                current_app.logger.warning("Password reset email failed for %s: %s", email, e)

        flash("If an account exists with that email, a reset link has been sent.")
        return redirect(url_for('auth.login'))

    return render_template("auth/forgot_password.html")


# --- Account Setup / Reset via Token ---

@auth_bp.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_token(token):
    if current_user.is_authenticated:
        return redirect(url_for('auth.home'))

    user = User.verify_reset_token(token)
    if user is None:
        # Try to decode token without time check so we can send them to their org's login
        user_id = User.get_user_id_from_reset_token_unsafe(token)
        if user_id:
            user = User.query.get(user_id)
            if user and getattr(user, "organization_id", None) and user.organization_id:
                org = Organization.query.get(user.organization_id)
                if org:
                    host = request.host.split(":")[0]
                    base_domain = host[4:] if host.startswith("www.") else host
                    login_url = f"https://{org.subdomain}.{base_domain}/login"
                    flash("That link has expired, but you can log in here at your Training Tracker.", "info")
                    return redirect(login_url)
        flash("That link is invalid or expired. Ask your manager to send a new one, or log in below.", "warning")
        return redirect(url_for('auth.login'))

    if request.method == "POST":
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if password != confirm_password:
            flash("Passwords do not match.")
            return render_template("auth/reset_token.html", token=token)

        user.set_password(password)
        db.session.commit()

        # If tenant user, send them to their org's login page so they can sign in right away
        if getattr(user, "organization_id", None) and user.organization_id:
            org = Organization.query.get(user.organization_id)
            if org:
                host = request.host.split(":")[0]
                base_domain = host[4:] if host.startswith("www.") else host
                login_url = f"https://{org.subdomain}.{base_domain}/login?password_set=1"
                return redirect(login_url)
        flash('Your password has been updated! You can now log in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template("auth/reset_token.html", token=token)


def _verify_support_token(token, max_age=3600):
    """Verify admin support/spectator token; return payload dict or None."""
    from itsdangerous import URLSafeTimedSerializer
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="admin-support-spectator")
    try:
        return s.loads(token, salt="admin-support-spectator", max_age=max_age)
    except Exception:
        return None


@auth_bp.get("/enter-support")
def enter_support():
    """On tenant: log in as target user (impersonation) using admin token."""
    if getattr(g, "current_organization_id", None) is None:
        abort(404)
    token = request.args.get("token")
    if not token:
        flash("Invalid or missing link.", "error")
        return redirect(url_for("auth.login"))
    payload = _verify_support_token(token)
    if not payload or payload.get("type") != "impersonate":
        flash("Link expired or invalid.", "error")
        return redirect(url_for("auth.login"))
    if payload.get("org_id") != g.current_organization_id:
        flash("Wrong organization.", "error")
        return redirect(url_for("auth.login"))
    user = User.query.get(payload.get("user_id"))
    if not user or user.organization_id != g.current_organization_id:
        flash("User not found.", "error")
        return redirect(url_for("auth.login"))
    session["_impersonating_admin_id"] = payload.get("admin_id")
    session["_impersonating"] = True
    login_user(user)
    session.permanent = True
    if user.role == "manager":
        return redirect(url_for("manager.dashboard"))
    return redirect(url_for("employee.dashboard"))


@auth_bp.get("/spectator-entry")
def spectator_entry():
    """On tenant: log in as target user in spectator mode (read-only, PII masked)."""
    if getattr(g, "current_organization_id", None) is None:
        abort(404)
    token = request.args.get("token")
    if not token:
        flash("Invalid or missing link.", "error")
        return redirect(url_for("auth.login"))
    payload = _verify_support_token(token)
    if not payload or payload.get("type") != "spectator":
        flash("Link expired or invalid.", "error")
        return redirect(url_for("auth.login"))
    if payload.get("org_id") != g.current_organization_id:
        flash("Wrong organization.", "error")
        return redirect(url_for("auth.login"))
    user = User.query.get(payload.get("user_id"))
    if not user or user.organization_id != g.current_organization_id:
        flash("User not found.", "error")
        return redirect(url_for("auth.login"))
    session["_spectator_admin_id"] = payload.get("admin_id")
    session["_spectator"] = True
    login_user(user)
    session.permanent = True
    if user.role == "manager":
        return redirect(url_for("manager.dashboard"))
    return redirect(url_for("employee.dashboard"))


@auth_bp.get("/exit-support")
def exit_support():
    """Exit impersonation or spectator; restore admin and redirect to main domain."""
    admin_id = session.pop("_impersonating_admin_id", None) or session.pop("_spectator_admin_id", None)
    session.pop("_impersonating", None)
    session.pop("_spectator", None)
    logout_user()
    if admin_id:
        admin = User.query.get(admin_id)
        if admin:
            login_user(admin)
            session.permanent = True
    scheme = current_app.config.get("PREFERRED_URL_SCHEME", "https")
    host = request.host.split(":")[0]
    base = host[4:] if host.startswith("www.") else host
    return redirect(f"{scheme}://{base}{url_for('admin.index')}")


@auth_bp.get("/logout")
@login_required
def logout():
    current_app.logger.info("Logout requested for user_id=%s", getattr(current_user, "id", None))
    logout_user()
    # Redirect to same host (subdomain) so user stays on company login, not main domain
    scheme = request.environ.get("wsgi.url_scheme") or current_app.config.get("PREFERRED_URL_SCHEME", "https")
    host = request.host or ""
    login_path = url_for("auth.login")
    resp = redirect(f"{scheme}://{host}{login_path}")
    # Clear the "remember me" cookie so the user is not auto-logged back in (e.g. in app WebView).
    cookie_name = current_app.config.get("REMEMBER_COOKIE_NAME", "remember_token")
    resp.delete_cookie(
        cookie_name,
        path="/",
        secure=current_app.config.get("REMEMBER_COOKIE_SECURE", False),
        samesite=current_app.config.get("REMEMBER_COOKIE_SAMESITE") or "Lax",
    )
    return resp


@auth_bp.route("/change_password", methods=["POST"])
@login_required
def change_password():
    new_password = request.form.get("new_password")
    confirm_password = request.form.get("confirm_password")

    if not new_password or new_password != confirm_password:
        flash("Passwords must match and cannot be empty.")
        return redirect(url_for('manager.employee_detail', employee_id=current_user.employee_id))

    current_user.set_password(new_password)
    db.session.commit()

    flash("Your password has been updated successfully!")
    return redirect(url_for('manager.employee_detail', employee_id=current_user.employee_id))


def _subdomain_slug(s):
    """Slugify for subdomain (alphanumeric + hyphen)."""
    if not s or not isinstance(s, str):
        return ""
    raw = "".join(c if c.isalnum() or c == "-" else "-" for c in s.strip().lower())
    return "-".join(p for p in raw.split("-") if p).strip("-")[:50] or "org"


@auth_bp.route("/waitlist", methods=["POST"])
def waitlist():
    """Capture landing page interest form submissions; auto-create org (pending_approval) and email the admin."""
    from datetime import datetime, timezone, timedelta
    from app.models import Organization, SystemSettings, User
    from app.utils.notifications import _enqueue_or_send_email

    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    business = data.get("business", "").strip()
    location_identifier = data.get("location_identifier", "").strip() or None
    phone = data.get("phone", "").strip() or None
    address_line1 = data.get("address_line1", "").strip() or None
    city = data.get("city", "").strip() or None
    state = data.get("state", "").strip() or None
    postal_code = data.get("postal_code", "").strip() or None
    size = data.get("size", "").strip()
    plan = (data.get("plan") or "").strip().lower() or None  # 'standard' | 'pro'
    if plan not in ("standard", "pro"):
        plan = "standard"

    message = "Thanks! We'll review and activate your account within 24 hours."
    if email:
        try:
            req = SignupRequest(
                name=name or "",
                email=email,
                business=business or "",
                location_identifier=location_identifier,
                phone=phone,
                address_line1=address_line1,
                city=city,
                state=state,
                postal_code=postal_code,
                size=size or None,
                plan=plan if plan in ("standard", "pro") else None,
            )
            db.session.add(req)
            db.session.flush()
            org_name = business or email.split("@")[0]
            base_sub = _subdomain_slug(org_name) or _subdomain_slug(email) or "org"
            subdomain = base_sub
            for n in range(100):
                if Organization.query.filter_by(subdomain=subdomain).first() is None:
                    break
                subdomain = f"{base_sub}-{req.id}" if n == 0 else f"{base_sub}-{n}"
            now = datetime.now(timezone.utc)
            trial_ends_at = now + timedelta(days=14)
            org = Organization(
                name=org_name,
                subdomain=subdomain,
                status="pending_approval",
                trial_ends_at=trial_ends_at,
                trial_plan=plan,
                billing_plan=plan,
            )
            db.session.add(org)
            db.session.flush()
            settings = SystemSettings(organization_id=org.id)
            db.session.add(settings)
            manager_user = User(
                organization_id=org.id,
                email=email,
                role="manager",
                employee_id=None,
            )
            db.session.add(manager_user)
            req.organization_id = org.id
            db.session.commit()
            try:
                import os
                admin_email = os.environ.get("ADMIN_EMAIL") or "support@trainingtracker.me"
                loc_part = f" — {location_identifier}" if location_identifier else ""
                subject = f"Training Tracker signup: {business or email}{loc_part}" + (f" ({plan})" if plan else "")
                # Plain text body so it displays correctly (no raw HTML in email client)
                body_lines = [
                    "New signup request (pending approval)",
                    "",
                    f"Name: {name}",
                    f"Email: {email}",
                    f"Business: {business}",
                    f"Subdomain: {subdomain}",
                ]
                if location_identifier:
                    body_lines.append(f"Location / store: {location_identifier}")
                if phone:
                    body_lines.append(f"Phone: {phone}")
                addr_parts = [a for a in [address_line1, city, state, postal_code] if a]
                if addr_parts:
                    body_lines.append(f"Address: {', '.join(addr_parts)}")
                body_lines.append(f"Team size: {size or '—'}")
                if plan:
                    body_lines.append(f"Plan: {plan.capitalize()}")
                body = "\n".join(body_lines)
                _enqueue_or_send_email(admin_email, subject, body)
            except Exception:
                pass
        except Exception:
            db.session.rollback()
            message = "Thanks for your interest. We'll be in touch soon."

    return jsonify({"ok": True, "message": message})