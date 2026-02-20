"""Platform admin: manage organizations. Only on main domain, superuser only."""
from flask import Blueprint, render_template, redirect, url_for, flash, request, g, abort
from flask_login import login_required, current_user
from app import db
from app.models import Organization, SystemSettings

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
    return render_template("admin/organization_detail.html", org=org)


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
