import os
import uuid
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, current_app, send_file, Response, abort
from flask_login import login_user, logout_user, login_required, current_user
from app.models import User, SystemSettings
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

# Prevent caching so navbar shows new logo immediately after saving in Settings (no refresh needed)
_NO_CACHE_HEADERS = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache", "Expires": "0"}


@auth_bp.get("/logo")
def logo():
    """Serve the configured system logo (no auth). Used by navbar and login page."""
    settings = SystemSettings.query.first()
    if not settings or not settings.custom_logo_url:
        abort(404)
    logo_url = (settings.custom_logo_url or "").strip()
    if not logo_url:
        abort(404)
    if logo_url.startswith("http"):
        data, ctype = _fetch_logo_image(logo_url)
        if data and ctype:
            r = Response(data, mimetype=ctype, direct_passthrough=True)
            r.headers.update(_NO_CACHE_HEADERS)
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
            r.headers.update(_NO_CACHE_HEADERS)
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
    r.headers.update(_NO_CACHE_HEADERS)
    return r


@auth_bp.get("/")
def home():
    if current_user.is_authenticated:
        if current_user.role == "manager":
            return redirect(url_for("manager.dashboard"))
        return redirect(url_for("employee.dashboard"))
    return render_template("landing.html")


@auth_bp.get("/tour")
def tour():
    return render_template("tour.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("auth.home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()

        if user and user.password_hash is None:
            flash("Your account is not set up yet. Please use the link sent to your email.")
            return redirect(url_for("auth.login"))

        if not user or not user.check_password(password):
            flash("Invalid email or password.")
            return render_template("login.html"), 401

        session.permanent = True
        session['_last_active'] = datetime.now(timezone.utc).timestamp()
        login_user(user)

        if user.role == "manager":
            return redirect(url_for("manager.dashboard"))
        return redirect(url_for("employee.dashboard"))

    return render_template("login.html")


# --- New: Forgot Password Request ---

@auth_bp.route("/forgot_password", methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('auth.home'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
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
                print(f"SMTP Error: {e}")

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
        flash('That is an invalid or expired token', 'warning')
        return redirect(url_for('auth.login'))

    if request.method == "POST":
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if password != confirm_password:
            flash("Passwords do not match.")
            return render_template("auth/reset_token.html", token=token)

        user.set_password(password)
        db.session.commit()

        flash('Your password has been updated! You can now log in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template("auth/reset_token.html", token=token)


@auth_bp.get("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


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


@auth_bp.route("/waitlist", methods=["POST"])
def waitlist():
    """Capture landing page interest form submissions and email the admin."""
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    email = data.get("email", "").strip()
    business = data.get("business", "").strip()
    size = data.get("size", "").strip()

    if email:
        try:
            from app.utils.notifications import send_notification_email
            import os
            admin_email = os.environ.get("ADMIN_EMAIL") or "adkins.adam04@gmail.com"
            subject = f"New Training Tracker Interest: {business or email}"
            body = (
                f"<h2>New Waitlist Signup</h2>"
                f"<p><strong>Name:</strong> {name}</p>"
                f"<p><strong>Email:</strong> {email}</p>"
                f"<p><strong>Business:</strong> {business}</p>"
                f"<p><strong>Team size:</strong> {size}</p>"
            )
            send_notification_email(admin_email, subject, body)
        except Exception:
            pass

    return jsonify({"ok": True})