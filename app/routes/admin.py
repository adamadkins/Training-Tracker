"""Platform admin: manage organizations. Only on main domain, superuser only."""
from flask import Blueprint, render_template, redirect, url_for, flash, request, g, abort
from flask_login import login_required, current_user
from app import db
from app.models import Organization, SystemSettings, User, Employee
from app.utils.notifications import send_notification_email

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _require_admin():
    """Require main domain and superuser. Call from before_request or top of each view."""
    if getattr(g, "current_organization_id", None) is not None:
        abort(404)
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    if not getattr(current_user, "is_superuser", False):
        abort(403)
    return None


@admin_bp.before_request
@login_required
def admin_before_request():
    r = _require_admin()
    if r is not None:
        return r


@admin_bp.route("/")
def index():
    """Dashboard: list organizations."""
    orgs = Organization.query.order_by(Organization.name).all()
    return render_template("admin/index.html", organizations=orgs)


@admin_bp.route("/organizations")
def organizations_list():
    """List all organizations."""
    orgs = Organization.query.order_by(Organization.name).all()
    return render_template("admin/organizations.html", organizations=orgs)


@admin_bp.route("/organizations/<int:org_id>")
def organization_detail(org_id):
    """View one organization."""
    org = Organization.query.get_or_404(org_id)
    user_count = User.query.filter_by(organization_id=org_id).count()
    return render_template("admin/organization_detail.html", org=org, has_users=user_count > 0)


@admin_bp.route("/organizations/new", methods=["GET", "POST"])
def organization_create():
    """Create a new organization (name + subdomain) and default SystemSettings."""
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        subdomain = (request.form.get("subdomain") or "").strip().lower()
        if not name or not subdomain:
            flash("Name and subdomain are required.", "error")
            return render_template("admin/organization_form.html")
        # Normalize subdomain: alphanumeric + hyphen
        subdomain = "".join(c for c in subdomain if c.isalnum() or c == "-").strip("-") or subdomain
        if not subdomain:
            flash("Subdomain must contain at least one letter or number.", "error")
            return render_template("admin/organization_form.html")
        existing = Organization.query.filter_by(subdomain=subdomain).first()
        if existing:
            flash(f"Subdomain '{subdomain}' is already in use.", "error")
            return render_template("admin/organization_form.html", name=name, subdomain=subdomain)
        org = Organization(name=name, subdomain=subdomain, status="active")
        db.session.add(org)
        db.session.flush()
        settings = SystemSettings(organization_id=org.id)
        db.session.add(settings)
        db.session.commit()
        host = request.host.split(":")[0]
        base_domain = host[4:] if host.startswith("www.") else host
        flash(f"Organization '{org.name}' created. Users can sign in at {subdomain}.{base_domain}.", "success")
        return redirect(url_for("admin.organization_detail", org_id=org.id))
    return render_template("admin/organization_form.html")


@admin_bp.route("/organizations/<int:org_id>/suspend", methods=["POST"])
def organization_suspend(org_id):
    """Set organization status to suspended (optional)."""
    org = Organization.query.get_or_404(org_id)
    org.status = "suspended"
    db.session.commit()
    flash(f"Organization '{org.name}' has been suspended.", "warning")
    return redirect(url_for("admin.organization_detail", org_id=org.id))


@admin_bp.route("/organizations/<int:org_id>/activate", methods=["POST"])
def organization_activate(org_id):
    """Set organization status to active."""
    org = Organization.query.get_or_404(org_id)
    org.status = "active"
    db.session.commit()
    flash(f"Organization '{org.name}' is active again.", "success")
    return redirect(url_for("admin.organization_detail", org_id=org.id))


def _base_domain():
    host = request.host.split(":")[0]
    return host[4:] if host.startswith("www.") else host


@admin_bp.route("/organizations/<int:org_id>/invite-first-user", methods=["GET", "POST"])
def organization_invite_first_user(org_id):
    """Create the first user (manager) for an org and send set-password invite."""
    org = Organization.query.get_or_404(org_id)
    if org.status != "active":
        flash("Organization must be active to invite users.", "error")
        return redirect(url_for("admin.organization_detail", org_id=org_id))
    existing = User.query.filter_by(organization_id=org_id).first()
    if existing:
        flash("This organization already has users. Use the tenant dashboard to invite more.", "info")
        return redirect(url_for("admin.organization_detail", org_id=org_id))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        first_name = (request.form.get("first_name") or "").strip()
        last_name = (request.form.get("last_name") or "").strip()
        if not email or not first_name or not last_name:
            flash("Email, first name, and last name are required.", "error")
            return render_template("admin/invite_first_user.html", org=org)
        if User.query.filter_by(organization_id=org_id, email=email).first():
            flash("A user with that email already exists for this organization.", "error")
            return render_template("admin/invite_first_user.html", org=org)
        try:
            emp = Employee(
                organization_id=org_id,
                first_name=first_name,
                last_name=last_name,
                role="manager",
                status="active",
            )
            db.session.add(emp)
            db.session.flush()
            user = User(email=email, role="manager", employee_id=emp.id, organization_id=org_id)
            db.session.add(user)
            db.session.commit()

            token = user.get_reset_token()
            set_password_url = url_for("auth.reset_token", token=token, _external=True)
            base = _base_domain()
            login_url = f"https://{org.subdomain}.{base}/login"
            title = "Set up your Training Tracker account"
            body = (
                f"Welcome to {org.name}!\n\n"
                f"You've been set up as a manager. Set your password by clicking the link below:\n\n"
                f"{set_password_url}\n\n"
                f"This link expires in 30 minutes. After setting your password, log in at:\n{login_url}"
            )
            send_notification_email(user, title, body)
            flash(f"Invitation sent to {email}. They can set their password and then log in at {org.subdomain}.{base}.", "success")
            return redirect(url_for("admin.organization_detail", org_id=org_id))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "error")
            return render_template("admin/invite_first_user.html", org=org)

    return render_template("admin/invite_first_user.html", org=org)
