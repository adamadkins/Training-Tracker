"""Platform admin: manage organizations. Only on main domain, superuser only."""
import stripe
from flask import Blueprint, render_template, redirect, url_for, flash, request, g, abort, current_app
from flask_login import login_required, current_user
from app import db
from app.models import (
    Organization, SystemSettings, User, Employee, Location, Position, PositionDescriptor,
    Daypart, Schedule, TrainingSession, Channel, Message, Notification, TrainingRoadmap,
    RoadmapStep, ChannelParticipant, MessageReaction, SessionRating, GuestTrainerToken,
    EmployeeNote, UserSettings, SignupRequest,
)
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


def _org_stats(org_id):
    """Return dict with user_count, employee_count for an org."""
    return {
        "user_count": User.query.filter_by(organization_id=org_id).count(),
        "employee_count": Employee.query.filter_by(organization_id=org_id).count(),
    }


@admin_bp.route("/")
def index():
    """Dashboard: key metrics and org list."""
    orgs = Organization.query.order_by(Organization.created_at.desc()).all()
    total_orgs = len(orgs)
    active_orgs = sum(1 for o in orgs if o.status == "active")
    total_users = User.query.filter(User.organization_id.isnot(None)).count()
    total_employees = Employee.query.filter(Employee.organization_id.isnot(None)).count()
    orgs_with_stats = [(org, _org_stats(org.id)) for org in orgs[:20]]
    signup_requests = SignupRequest.query.order_by(SignupRequest.created_at.desc()).limit(50).all()
    return render_template(
        "admin/index.html",
        organizations=orgs_with_stats,
        total_orgs=total_orgs,
        active_orgs=active_orgs,
        total_users=total_users,
        total_employees=total_employees,
        signup_requests=signup_requests,
    )


@admin_bp.route("/organizations")
def organizations_list():
    """List all organizations with stats."""
    orgs = Organization.query.order_by(Organization.name).all()
    orgs_with_stats = [(org, _org_stats(org.id)) for org in orgs]
    return render_template("admin/organizations.html", organizations=orgs_with_stats)


@admin_bp.route("/organizations/<int:org_id>")
def organization_detail(org_id):
    """View one organization with full stats."""
    org = Organization.query.get_or_404(org_id)
    stats = _org_stats(org_id)
    has_users = stats["user_count"] > 0
    if request.args.get("billing") == "success":
        flash("Billing set up successfully.", "success")
    elif request.args.get("billing") == "canceled":
        flash("Checkout was canceled.", "info")
    return render_template(
        "admin/organization_detail.html",
        org=org,
        has_users=has_users,
        user_count=stats["user_count"],
        employee_count=stats["employee_count"],
    )


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


@admin_bp.route("/organizations/<int:org_id>/free-plan/<plan>", methods=["POST"])
def organization_set_free_plan(org_id, plan):
    """Give organization Standard or Pro plan for free. plan is 'standard' or 'pro'."""
    org = Organization.query.get_or_404(org_id)
    if plan not in ("standard", "pro"):
        flash("Invalid plan.", "error")
        return redirect(url_for("admin.organization_detail", org_id=org_id))
    org.free_plan = plan
    org.billing_plan = plan
    db.session.commit()
    flash(f"Organization '{org.name}' now has {plan.capitalize()} plan (free).", "success")
    return redirect(url_for("admin.organization_detail", org_id=org_id))


@admin_bp.route("/organizations/<int:org_id>/remove-free-plan", methods=["POST"])
def organization_remove_free_plan(org_id):
    """Remove free plan; org will need a paid subscription or to be set up again."""
    org = Organization.query.get_or_404(org_id)
    org.free_plan = None
    if not org.stripe_subscription_id:
        org.billing_plan = None
    db.session.commit()
    flash(f"Free plan removed for '{org.name}'.", "info")
    return redirect(url_for("admin.organization_detail", org_id=org_id))


@admin_bp.route("/organizations/<int:org_id>/create-checkout", methods=["POST"])
def organization_create_checkout(org_id):
    """Redirect to Stripe Checkout to start a subscription for this org. Plan: standard | pro."""
    org = Organization.query.get_or_404(org_id)
    secret = current_app.config.get("STRIPE_SECRET_KEY")
    plan = (request.form.get("plan") or "standard").strip().lower()
    if plan == "pro":
        price_id = current_app.config.get("STRIPE_PRICE_ID_PRO")
    else:
        price_id = current_app.config.get("STRIPE_PRICE_ID_STANDARD") or current_app.config.get("STRIPE_PRICE_ID")
    if not secret or not price_id:
        flash("Stripe is not configured (STRIPE_SECRET_KEY and STRIPE_PRICE_ID_STANDARD or STRIPE_PRICE_ID).", "error")
        return redirect(url_for("admin.organization_detail", org_id=org_id))
    stripe.api_key = secret
    base = _base_domain()
    scheme = current_app.config.get("PREFERRED_URL_SCHEME", "https")
    admin_org_url = f"{scheme}://{request.host.split(':')[0]}{url_for('admin.organization_detail', org_id=org_id)}"
    success_url = f"{admin_org_url}?billing=success"
    cancel_url = f"{admin_org_url}?billing=canceled"
    customer_email = None
    first_user = User.query.filter_by(organization_id=org_id).first()
    if first_user:
        customer_email = first_user.email
    try:
        session_params = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "client_reference_id": str(org_id),
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        if org.stripe_customer_id:
            session_params["customer"] = org.stripe_customer_id
        elif customer_email:
            session_params["customer_email"] = customer_email
        session = stripe.checkout.Session.create(**session_params)
        return redirect(session.url)
    except stripe.StripeError as e:
        flash(f"Stripe error: {str(e)}", "error")
        return redirect(url_for("admin.organization_detail", org_id=org_id))


@admin_bp.route("/organizations/<int:org_id>/customer-portal", methods=["POST"])
def organization_customer_portal(org_id):
    """Redirect to Stripe Customer Portal so the customer can manage payment / cancel."""
    org = Organization.query.get_or_404(org_id)
    if not org.stripe_customer_id:
        flash("No billing customer linked. Set up billing first.", "error")
        return redirect(url_for("admin.organization_detail", org_id=org_id))
    secret = current_app.config.get("STRIPE_SECRET_KEY")
    if not secret:
        flash("Stripe is not configured.", "error")
        return redirect(url_for("admin.organization_detail", org_id=org_id))
    stripe.api_key = secret
    scheme = current_app.config.get("PREFERRED_URL_SCHEME", "https")
    return_url = f"{scheme}://{request.host.split(':')[0]}{url_for('admin.organization_detail', org_id=org_id)}"
    try:
        portal = stripe.billing_portal.Session.create(
            customer=org.stripe_customer_id,
            return_url=return_url,
        )
        return redirect(portal.url)
    except stripe.StripeError as e:
        flash(f"Stripe error: {str(e)}", "error")
        return redirect(url_for("admin.organization_detail", org_id=org_id))


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
                f"You've been set up as a manager. Click the link below to set your password. "
                f"You'll then be taken to your sign-in page to log in.\n\n"
                f"{set_password_url}\n\n"
                f"This link expires in 30 minutes."
            )
            send_notification_email(user, title, body)
            flash(f"Invitation sent to {email}. They can set their password and then log in at {org.subdomain}.{base}.", "success")
            return redirect(url_for("admin.organization_detail", org_id=org_id))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "error")
            return render_template("admin/invite_first_user.html", org=org)

    return render_template("admin/invite_first_user.html", org=org)


@admin_bp.route("/organizations/<int:org_id>/delete", methods=["POST"])
def organization_delete(org_id):
    """Permanently delete an organization and all its data. Requires confirmation."""
    org = Organization.query.get_or_404(org_id)
    confirm = (request.form.get("confirm") or "").strip().lower()
    if confirm != "delete":
        flash("Deletion not confirmed. Type 'delete' in the confirmation field.", "error")
        return redirect(url_for("admin.organization_detail", org_id=org_id))
    name, subdomain = org.name, org.subdomain
    try:
        user_ids = [u.id for u in User.query.filter_by(organization_id=org_id).all()]
        employee_ids = [e.id for e in Employee.query.filter_by(organization_id=org_id).all()]
        channel_ids = [c.id for c in Channel.query.filter_by(organization_id=org_id).all()]

        if user_ids:
            Notification.query.filter(Notification.user_id.in_(user_ids)).delete(synchronize_session=False)
            UserSettings.query.filter(UserSettings.user_id.in_(user_ids)).delete(synchronize_session=False)
        if channel_ids:
            msg_ids = db.session.query(Message.id).filter(Message.channel_id.in_(channel_ids))
            MessageReaction.query.filter(MessageReaction.message_id.in_(msg_ids)).delete(synchronize_session=False)
            Message.query.filter(Message.channel_id.in_(channel_ids)).delete(synchronize_session=False)
            ChannelParticipant.query.filter(ChannelParticipant.channel_id.in_(channel_ids)).delete(synchronize_session=False)
        Channel.query.filter_by(organization_id=org_id).delete(synchronize_session=False)
        session_ids = [s.id for s in TrainingSession.query.filter_by(organization_id=org_id).all()]
        if session_ids:
            SessionRating.query.filter(SessionRating.training_session_id.in_(session_ids)).delete(synchronize_session=False)
        TrainingSession.query.filter_by(organization_id=org_id).delete(synchronize_session=False)
        if employee_ids:
            GuestTrainerToken.query.filter(GuestTrainerToken.trainee_id.in_(employee_ids)).delete(synchronize_session=False)
            EmployeeNote.query.filter(EmployeeNote.trainee_employee_id.in_(employee_ids)).delete(synchronize_session=False)
        roadmap_ids = [r.id for r in TrainingRoadmap.query.filter_by(organization_id=org_id).all()]
        if roadmap_ids:
            RoadmapStep.query.filter(RoadmapStep.roadmap_id.in_(roadmap_ids)).delete(synchronize_session=False)
        TrainingRoadmap.query.filter_by(organization_id=org_id).delete(synchronize_session=False)
        Employee.query.filter_by(organization_id=org_id).update({Employee.current_roadmap_id: None}, synchronize_session=False)
        Schedule.query.filter_by(organization_id=org_id).delete(synchronize_session=False)
        User.query.filter_by(organization_id=org_id).delete(synchronize_session=False)
        Employee.query.filter_by(organization_id=org_id).delete(synchronize_session=False)
        Location.query.filter_by(organization_id=org_id).delete(synchronize_session=False)
        position_ids = [p.id for p in Position.query.filter_by(organization_id=org_id).all()]
        if position_ids:
            PositionDescriptor.query.filter(PositionDescriptor.position_id.in_(position_ids)).delete(synchronize_session=False)
        Position.query.filter_by(organization_id=org_id).delete(synchronize_session=False)
        Daypart.query.filter_by(organization_id=org_id).delete(synchronize_session=False)
        SystemSettings.query.filter_by(organization_id=org_id).delete(synchronize_session=False)
        db.session.delete(org)
        db.session.commit()
        flash(f"Organization '{name}' ({subdomain}) has been permanently deleted.", "success")
        return redirect(url_for("admin.organizations_list"))
    except Exception as e:
        db.session.rollback()
        flash(f"Delete failed: {str(e)}", "error")
        return redirect(url_for("admin.organization_detail", org_id=org_id))
