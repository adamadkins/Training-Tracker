"""Platform admin: manage organizations. Only on main domain, superuser only."""
import stripe
from urllib.parse import quote
from datetime import datetime, timezone, timedelta
from itsdangerous import URLSafeTimedSerializer
from flask import Blueprint, render_template, redirect, url_for, flash, request, g, abort, current_app
from flask_login import login_required, current_user
from app import db
from app.models import (
    Organization, SystemSettings, User, Employee, Location, Position, PositionDescriptor,
    Daypart, Schedule, TrainingSession, Channel, Message, Notification, TrainingRoadmap,
    RoadmapStep, ChannelParticipant, MessageReaction, SessionRating, GuestTrainerToken,
    EmployeeNote, UserSettings, SignupRequest, SupportRequest,
)
from app.utils.notifications import send_notification_email

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _require_admin():
    """Require main domain and superuser. Call from before_request or top of each view."""
    if getattr(g, "current_organization_id", None) is not None:
        # On tenant subdomain, redirect to main domain /admin so they can log in there (platform admin only on main).
        main_domain = current_app.config.get("MAIN_DOMAIN", "trainingtracker.me").split(":")[0]
        return redirect(f"https://{main_domain}/admin")
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


def _org_users(org_id):
    """Return list of users (with email) for an org, for display in admin."""
    return User.query.filter_by(organization_id=org_id).order_by(User.email).all()


def _org_trial_and_payment(org):
    """Return dict with trial_days_left, payment_overdue, payment_status, and billing_plan."""
    trial_days_left = None
    if org.trial_ends_at:
        end = org.trial_ends_at
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        delta = (end - datetime.now(timezone.utc)).days
        trial_days_left = max(0, delta)
    sub_status = (org.stripe_subscription_status or "").lower()
    payment_overdue = sub_status in ("past_due", "unpaid")
    if org.free_plan:
        payment_status = "free"
    elif sub_status == "active":
        payment_status = "active"
    elif sub_status == "trialing":
        payment_status = "trialing"
    elif payment_overdue:
        payment_status = "overdue"
    elif sub_status == "canceled":
        payment_status = "canceled"
    elif org.stripe_subscription_id:
        payment_status = sub_status or "unknown"
    else:
        payment_status = None
    return {
        "trial_days_left": trial_days_left,
        "payment_overdue": payment_overdue,
        "payment_status": payment_status,
        "billing_plan": org.billing_plan,
    }


def _signup_request_email_content(signup_request):
    """Return (subject, body) for the signup request based on plan."""
    r = signup_request
    subject = "Your Training Tracker access request"
    name_part = (" " + r.name) if r.name else ""
    addr_parts = [a for a in [r.address_line1, r.city, r.state, r.postal_code] if a]
    location_line = ""
    if r.location_identifier or addr_parts:
        loc = r.location_identifier or ""
        addr = ", ".join(addr_parts) if addr_parts else ""
        location_line = f"Location: {loc}" + (f" — {addr}" if addr else "") + "\n\n"
    trial_line = "I'm going to set you up with a 14-day free trial, then you'll be on the plan you chose.\n\n"
    if r.plan == "standard":
        body = (
            f"Hi{name_part},\n\n"
            "Thanks for your interest in Training Tracker Standard. I'd love to get you set up.\n\n"
            + location_line
            + trial_line
            + "Standard includes everything you need to manage training and schedules for your team. "
            "I'll send you an invite to create your organization shortly.\n\n"
            "Best,"
        )
    elif r.plan == "pro":
        body = (
            f"Hi{name_part},\n\n"
            "Thanks for your interest in Training Tracker Pro. I'd love to get you set up.\n\n"
            + location_line
            + trial_line
            + "Pro includes PDF schedule upload and advanced features on top of everything in Standard. "
            "I'll send you an invite to create your organization shortly.\n\n"
            "Best,"
        )
    else:
        body = (
            f"Hi{name_part},\n\n"
            "Thanks for reaching out about Training Tracker. I'd be happy to help you get started.\n\n"
            + location_line
            + trial_line
            + "I'll follow up with next steps shortly.\n\n"
            "Best,"
        )
    return subject, body


def _signup_request_mailto(signup_request):
    """Build a mailto URL (opens default desktop mail client)."""
    r = signup_request
    email = (r.email or "").strip()
    subject, body = _signup_request_email_content(r)
    body = body.replace("\r\n", "\n").replace("\n", "\r\n")
    return (
        "mailto:" + quote(email, safe="")
        + "?subject=" + quote(subject, safe="")
        + "&body=" + quote(body, safe="")
    )


def _signup_request_outlook_url(signup_request):
    """Build Outlook Web compose URL so the Email button opens Outlook in the browser."""
    r = signup_request
    email = (r.email or "").strip()
    subject, body = _signup_request_email_content(r)
    body = body.replace("\r\n", "\n")
    base = "https://outlook.office.com/mail/deeplink/compose"
    return (
        base
        + "?to=" + quote(email, safe="")
        + "&subject=" + quote(subject, safe="")
        + "&body=" + quote(body, safe="")
    )


@admin_bp.route("/")
def index():
    """Dashboard: key metrics and org list. Optional q (search), org_status, signup_plan."""
    q = (request.args.get("q") or "").strip()
    org_status = request.args.get("org_status", "").strip().lower() or None
    signup_plan = request.args.get("signup_plan", "").strip().lower() or None

    orgs = Organization.query.order_by(Organization.created_at.desc()).all()
    if org_status and org_status in ("active", "suspended", "pending_approval"):
        orgs = [o for o in orgs if o.status == org_status]
    pending_orgs_count = Organization.query.filter_by(status="pending_approval").count()
    if q:
        ql = q.lower()
        orgs = [o for o in orgs if ql in (o.name or "").lower() or ql in (o.subdomain or "").lower()]
    total_orgs = len(Organization.query.all())
    active_orgs = Organization.query.filter_by(status="active").count()
    total_users = User.query.filter(User.organization_id.isnot(None)).count()
    total_employees = Employee.query.filter(Employee.organization_id.isnot(None)).count()

    # Revenue stats
    plan_prices = {"standard": 40, "pro": 50}
    all_orgs_for_revenue = Organization.query.all()
    mrr = 0
    paid_orgs_count = 0
    free_orgs_count = 0
    for o in all_orgs_for_revenue:
        sub_st = (o.stripe_subscription_status or "").lower()
        if o.free_plan:
            free_orgs_count += 1
        elif o.stripe_subscription_id and sub_st in ("active", "trialing"):
            paid_orgs_count += 1
            mrr += plan_prices.get((o.billing_plan or "").lower(), 0)
    arr = mrr * 12

    orgs_with_stats = [
        (org, _org_stats(org.id), _org_trial_and_payment(org), _org_users(org.id))
        for org in orgs[:10]
    ]

    signup_query = SignupRequest.query.order_by(SignupRequest.created_at.desc())
    if signup_plan and signup_plan in ("standard", "pro"):
        signup_query = signup_query.filter_by(plan=signup_plan)
    signup_search = (request.args.get("signup_q") or "").strip()
    if signup_search:
        s = f"%{signup_search}%"
        signup_query = signup_query.filter(
            db.or_(
                SignupRequest.email.ilike(s),
                SignupRequest.business.ilike(s),
                SignupRequest.name.ilike(s),
            )
        )
    signup_requests = signup_query.limit(50).all()
    signup_requests_with_mailto = [
        (r, _signup_request_mailto(r), _signup_request_outlook_url(r)) for r in signup_requests
    ]
    return render_template(
        "admin/index.html",
        organizations=orgs_with_stats,
        total_orgs=total_orgs,
        active_orgs=active_orgs,
        pending_orgs_count=pending_orgs_count,
        total_users=total_users,
        total_employees=total_employees,
        mrr=mrr,
        arr=arr,
        paid_orgs_count=paid_orgs_count,
        free_orgs_count=free_orgs_count,
        signup_requests_with_mailto=signup_requests_with_mailto,
        filter_q=q,
        filter_org_status=org_status,
        filter_signup_plan=signup_plan,
        filter_signup_q=signup_search,
    )


@admin_bp.route("/signup-requests/<int:req_id>/update", methods=["POST"])
def signup_request_update(req_id):
    """Update follow-up status and notes for a signup request."""
    req = SignupRequest.query.get_or_404(req_id)
    new_status = request.form.get("status")
    notes = request.form.get("notes")
    
    if new_status and new_status in ("new", "contacted", "resolved"):
        req.status = new_status
    if notes is not None:
        req.notes = notes.strip()
        
    db.session.commit()
    flash(f"Updated follow-up tracking for {req.business or req.email}.", "success")
    return redirect(url_for("admin.index"))


@admin_bp.route("/signup-requests/clear", methods=["POST"])
def signup_requests_clear():
    """Delete all signup requests."""
    count = SignupRequest.query.delete()
    db.session.commit()
    flash(f"Cleared {count} signup request(s).", "success")
    return redirect(url_for("admin.index"))


@admin_bp.route("/signup-requests/<int:req_id>/delete", methods=["POST"])
def signup_request_delete(req_id):
    """Delete one signup request."""
    req = SignupRequest.query.get_or_404(req_id)
    db.session.delete(req)
    db.session.commit()
    flash("Signup request removed.", "success")
    return redirect(url_for("admin.index"))


@admin_bp.route("/users")
def users_list():
    """List all platform users (by org). Optional q (search), org_id, role filter."""
    q = (request.args.get("q") or "").strip()
    org_id = request.args.get("org_id", type=int)
    role = (request.args.get("role") or "").strip().lower() or None

    users = User.query.filter(User.organization_id.isnot(None))
    if org_id:
        users = users.filter(User.organization_id == org_id)
    if role and role in ("manager", "trainee"):
        users = users.filter(User.role == role)
    users = users.all()
    org_ids = [u.organization_id for u in users]
    orgs_map = {o.id: o for o in Organization.query.filter(Organization.id.in_(org_ids)).all()} if org_ids else {}
    users_with_org = [(u, orgs_map.get(u.organization_id)) for u in users]
    if q:
        ql = q.lower()
        users_with_org = [(u, o) for u, o in users_with_org if ql in (u.email or "").lower()]
    users_with_org.sort(key=lambda x: ((x[1].name or "") if x[1] else "", x[0].email or ""))
    all_orgs = Organization.query.order_by(Organization.name).all()
    return render_template(
        "admin/users.html",
        users_with_org=users_with_org,
        all_orgs=all_orgs,
        filter_q=q,
        filter_org_id=org_id,
        filter_role=role,
    )


@admin_bp.route("/support-requests")
def support_requests_list():
    """List support/contact form submissions from the public support page (newest first)."""
    requests = SupportRequest.query.order_by(SupportRequest.created_at.desc()).all()
    return render_template("admin/support_requests.html", support_requests=requests)


@admin_bp.route("/organizations")
def organizations_list():
    """List all organizations with stats. Optional q (search), status filter."""
    q = (request.args.get("q") or "").strip()
    status_filter = (request.args.get("status") or "").strip().lower() or None
    orgs = Organization.query.order_by(Organization.name).all()
    if status_filter and status_filter in ("active", "suspended", "pending_approval"):
        orgs = [o for o in orgs if o.status == status_filter]
    if q:
        ql = q.lower()
        orgs = [o for o in orgs if ql in (o.name or "").lower() or ql in (o.subdomain or "").lower()]
    orgs_with_stats = [(org, _org_stats(org.id), _org_trial_and_payment(org)) for org in orgs]
    return render_template(
        "admin/organizations.html",
        organizations=orgs_with_stats,
        filter_q=q,
        filter_status=status_filter,
    )


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
    trial_payment = _org_trial_and_payment(org)
    org_users = _org_users(org_id)
    return render_template(
        "admin/organization_detail.html",
        org=org,
        has_users=has_users,
        user_count=stats["user_count"],
        employee_count=stats["employee_count"],
        org_users=org_users,
        trial_days_left=trial_payment["trial_days_left"],
        payment_overdue=trial_payment["payment_overdue"],
        payment_status=trial_payment.get("payment_status"),
        billing_plan=trial_payment.get("billing_plan"),
    )


def _subdomain_slug(s):
    """Slugify a string for use as subdomain (e.g. 'Store #4521' -> 'store-4521')."""
    if not s or not isinstance(s, str):
        return ""
    raw = "".join(c if c.isalnum() or c == "-" else "-" for c in s.strip().lower())
    return "-".join(p for p in raw.split("-") if p).strip("-") or ""


def _split_name(full_name):
    """Return (first_name, last_name) from full name; last name may be empty."""
    parts = (full_name or "").strip().split(None, 1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


@admin_bp.route("/organizations/new", methods=["GET", "POST"])
def organization_create():
    """Create a new organization (name + subdomain), default SystemSettings, first manager user, and send invite email."""
    signup_requests = SignupRequest.query.order_by(SignupRequest.created_at.desc()).limit(25).all()
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        subdomain = (request.form.get("subdomain") or "").strip().lower()
        trial_plan_param = request.form.get("trial_plan")
        manager_email = (request.form.get("manager_email") or "").strip().lower()
        manager_first_name = (request.form.get("manager_first_name") or "").strip()
        manager_last_name = (request.form.get("manager_last_name") or "").strip()
        from_id = request.args.get("from")
        
        if not name or not subdomain:
            flash("Name and subdomain are required.", "error")
            return render_template("admin/organization_form.html", name=name, subdomain=subdomain, trial_plan=trial_plan_param, manager_email=manager_email, manager_first_name=manager_first_name, manager_last_name=manager_last_name, signup_requests=signup_requests, prefilled_signup=None)
        if not manager_email or not manager_first_name or not manager_last_name:
            flash("Manager email, first name, and last name are required.", "error")
            return render_template("admin/organization_form.html", name=name, subdomain=subdomain, trial_plan=trial_plan_param, manager_email=manager_email, manager_first_name=manager_first_name, manager_last_name=manager_last_name, signup_requests=signup_requests, prefilled_signup=None)
        # Normalize subdomain: alphanumeric + hyphen
        subdomain = "".join(c for c in subdomain if c.isalnum() or c == "-").strip("-") or subdomain
        if not subdomain:
            flash("Subdomain must contain at least one letter or number.", "error")
            return render_template("admin/organization_form.html", name=name, subdomain=subdomain, trial_plan=trial_plan_param, manager_email=manager_email, manager_first_name=manager_first_name, manager_last_name=manager_last_name, signup_requests=signup_requests, prefilled_signup=None)
        existing = Organization.query.filter_by(subdomain=subdomain).first()
        if existing:
            flash(f"Subdomain '{subdomain}' is already in use.", "error")
            return render_template("admin/organization_form.html", name=name, subdomain=subdomain, trial_plan=trial_plan_param, manager_email=manager_email, manager_first_name=manager_first_name, manager_last_name=manager_last_name, signup_requests=signup_requests, prefilled_signup=None)
        if User.query.filter_by(email=manager_email).first():
            flash(f"A user with email '{manager_email}' already exists. Use a different email or add them from their existing organization.", "error")
            return render_template("admin/organization_form.html", name=name, subdomain=subdomain, trial_plan=trial_plan_param, manager_email=manager_email, manager_first_name=manager_first_name, manager_last_name=manager_last_name, signup_requests=signup_requests, prefilled_signup=None)
        trial_plan = (request.form.get("trial_plan") or "standard").strip().lower()
        if trial_plan not in ("standard", "pro"):
            trial_plan = "standard"
        now = datetime.now(timezone.utc)
        trial_ends_at = now + timedelta(days=14)
        try:
            org = Organization(
                name=name,
                subdomain=subdomain,
                status="active",
                trial_ends_at=trial_ends_at,
                trial_plan=trial_plan,
                billing_plan=trial_plan,
            )
            db.session.add(org)
            db.session.flush()
            settings = SystemSettings(organization_id=org.id)
            db.session.add(settings)
            emp = Employee(
                organization_id=org.id,
                first_name=manager_first_name,
                last_name=manager_last_name,
                role="manager",
                status="active",
            )
            db.session.add(emp)
            db.session.flush()
            user = User(email=manager_email, role="manager", employee_id=emp.id, organization_id=org.id)
            db.session.add(user)
            
            if from_id:
                try:
                    req = SignupRequest.query.get(int(from_id))
                    if req:
                        req.organization_id = org.id
                except (TypeError, ValueError):
                    pass
            
            db.session.commit()
            # Send invite email (set-password link)
            try:
                from app.routes.manager import send_invite_email
                send_invite_email(user)
            except Exception as e:
                current_app.logger.warning("Invite email failed after org create: %s", e)
            host = request.host.split(":")[0]
            base_domain = host[4:] if host.startswith("www.") else host
            flash(f"Organization '{org.name}' created. Invite sent to {manager_email} — they can set their password and sign in at {subdomain}.{base_domain}.", "success")
            return redirect(url_for("admin.organization_detail", org_id=org.id))
        except Exception as e:
            db.session.rollback()
            current_app.logger.exception("Organization create failed: %s", e)
            flash(f"Could not create organization: {e}", "error")
            return render_template("admin/organization_form.html", name=name, subdomain=subdomain, trial_plan=trial_plan_param, manager_email=manager_email, manager_first_name=manager_first_name, manager_last_name=manager_last_name, signup_requests=signup_requests, prefilled_signup=None)
    # GET: optionally pre-fill from signup request
    prefilled_signup = None
    from_id = request.args.get("from")
    if from_id:
        try:
            prefilled_signup = SignupRequest.query.get(int(from_id))
        except (TypeError, ValueError):
            pass
    manager_email = ""
    manager_first_name = ""
    manager_last_name = ""
    if prefilled_signup:
        manager_email = (prefilled_signup.email or "").strip().lower()
        manager_first_name, manager_last_name = _split_name(prefilled_signup.name)
    return render_template(
        "admin/organization_form.html",
        name=(prefilled_signup.business or "").strip() if prefilled_signup else "",
        subdomain=_subdomain_slug((prefilled_signup.location_identifier or prefilled_signup.business or "") if prefilled_signup else "") if prefilled_signup else "",
        trial_plan=(prefilled_signup.plan or "standard") if prefilled_signup else "standard",
        manager_email=manager_email,
        manager_first_name=manager_first_name,
        manager_last_name=manager_last_name,
        signup_requests=signup_requests,
        prefilled_signup=prefilled_signup,
    )


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
    now = datetime.now(timezone.utc)
    sub_status = (org.stripe_subscription_status or "").lower()
    has_paid_plan = bool(org.free_plan) or (
        bool(org.stripe_subscription_id)
        and sub_status not in ("past_due", "canceled", "unpaid", "incomplete_expired")
    )
    org.status = "active"
    # If billing is not linked yet, extend access so manual activation does not immediately re-suspend.
    if not has_paid_plan:
        trial_end = org.trial_ends_at
        if trial_end and trial_end.tzinfo is None:
            trial_end = trial_end.replace(tzinfo=timezone.utc)
        if not trial_end or trial_end <= now:
            org.trial_ends_at = now + timedelta(days=30)
        # reset so the trial-ended email can be sent again at the next real expiration, if needed
        org.trial_expiration_email_sent = False
    db.session.commit()
    if has_paid_plan:
        flash(f"Organization '{org.name}' is active again.", "success")
    else:
        flash(
            f"Organization '{org.name}' is active again. Trial access was extended 30 days because billing is not linked yet.",
            "warning",
        )
    return redirect(url_for("admin.organization_detail", org_id=org.id))


@admin_bp.route("/organizations/<int:org_id>/approve", methods=["POST"])
def organization_approve(org_id):
    """Approve a pending_approval org: set active and send invite/welcome email to the manager."""
    org = Organization.query.get_or_404(org_id)
    if org.status != "pending_approval":
        flash("Organization is not pending approval.", "info")
        return redirect(url_for("admin.organization_detail", org_id=org.id))
    org.status = "active"
    manager_user = User.query.filter_by(organization_id=org.id, role="manager").first()
    if not manager_user:
        manager_user = User.query.filter_by(organization_id=org.id).first()
    if manager_user:
        from app.routes.manager import send_invite_email
        try:
            send_invite_email(manager_user)
        except Exception:
            pass
    db.session.commit()
    flash(f"Organization '{org.name}' approved. Welcome email sent to manager.", "success")
    return redirect(url_for("admin.organization_detail", org_id=org.id))


@admin_bp.route("/organizations/<int:org_id>/extend-trial", methods=["POST"])
def organization_extend_trial(org_id):
    """Extend or set trial end date. If trial ended, set to now + days; else add days."""
    org = Organization.query.get_or_404(org_id)
    try:
        days = int(request.form.get("days", 14))
        days = max(1, min(365, days))
    except (TypeError, ValueError):
        days = 14
    now = datetime.now(timezone.utc)
    if org.trial_ends_at:
        end = org.trial_ends_at
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if end > now:
            org.trial_ends_at = end + timedelta(days=days)
        else:
            org.trial_ends_at = now + timedelta(days=days)
    else:
        org.trial_ends_at = now + timedelta(days=days)
        if not org.trial_plan:
            org.trial_plan = "standard"
        if not org.billing_plan:
            org.billing_plan = org.trial_plan
    db.session.commit()
    flash(f"Trial extended by {days} days for '{org.name}'.", "success")
    return redirect(url_for("admin.organization_detail", org_id=org.id))


@admin_bp.route("/organizations/<int:org_id>/set-trial-days", methods=["POST"])
def organization_set_trial_days(org_id):
    """Set trial to end in exactly X days (for testing). Overwrites current trial end."""
    org = Organization.query.get_or_404(org_id)
    try:
        days = int(request.form.get("days_left", 14))
        days = max(0, min(365, days))  # 0 = end trial now
    except (TypeError, ValueError):
        days = 14
    now = datetime.now(timezone.utc)
    if days == 0:
        org.trial_ends_at = now
        db.session.commit()
        flash(f"Trial set to ended now for '{org.name}'.", "success")
    else:
        org.trial_ends_at = now + timedelta(days=days)
        if not org.trial_plan:
            org.trial_plan = "standard"
        if not org.billing_plan:
            org.billing_plan = org.trial_plan
        db.session.commit()
        flash(f"Trial set to {days} day(s) left for '{org.name}'.", "success")
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


def _support_token_serializer():
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"],
        salt="admin-support-spectator",
    )


def _create_support_token(admin_id, user_id, org_id, kind, expires_sec=3600):
    """Create signed token for impersonate or spectator. kind = 'impersonate' | 'spectator'."""
    s = _support_token_serializer()
    return s.dumps({"admin_id": admin_id, "user_id": user_id, "org_id": org_id, "type": kind})


@admin_bp.route("/users/<int:user_id>/send-reset", methods=["POST"])
def user_send_reset(user_id):
    """Send password reset email to this user (support)."""
    user = User.query.get_or_404(user_id)
    if not user.organization_id:
        flash("Cannot send reset for platform admins.", "error")
        return redirect(url_for("admin.users_list"))
    try:
        token = user.get_reset_token()
        reset_url = url_for("auth.reset_token", token=token, _external=True)
        send_notification_email(
            user,
            "Reset your Training Tracker password",
            f"Someone requested a password reset. If it was you, click the link below.\n\n{reset_url}\n\nThis link expires in 7 days.",
            category='password_reset',
        )
        flash(f"Password reset email sent to {user.email}.", "success")
    except Exception as e:
        flash(f"Failed to send email: {str(e)}", "error")
    return redirect(request.referrer or url_for("admin.users_list"))


@admin_bp.route("/users/<int:user_id>/impersonate")
def user_impersonate(user_id):
    """Redirect to tenant as this user (full access, for support)."""
    user = User.query.get_or_404(user_id)
    if not user.organization_id:
        flash("Cannot impersonate platform admins.", "error")
        return redirect(url_for("admin.users_list"))
    org = Organization.query.get(user.organization_id)
    if not org:
        flash("Organization not found.", "error")
        return redirect(url_for("admin.users_list"))
    token = _create_support_token(current_user.id, user.id, org.id, "impersonate")
    scheme = current_app.config.get("PREFERRED_URL_SCHEME", "https")
    base = _base_domain()
    url = f"{scheme}://{org.subdomain}.{base}/enter-support?token={quote(token)}"
    return redirect(url)


@admin_bp.route("/users/<int:user_id>/spectate")
def user_spectate(user_id):
    """Redirect to tenant as this user in spectator mode (read-only, PII masked)."""
    user = User.query.get_or_404(user_id)
    if not user.organization_id:
        flash("Cannot spectate platform admins.", "error")
        return redirect(url_for("admin.users_list"))
    org = Organization.query.get(user.organization_id)
    if not org:
        flash("Organization not found.", "error")
        return redirect(url_for("admin.users_list"))
    token = _create_support_token(current_user.id, user.id, org.id, "spectator")
    scheme = current_app.config.get("PREFERRED_URL_SCHEME", "https")
    base = _base_domain()
    url = f"{scheme}://{org.subdomain}.{base}/spectator-entry?token={quote(token)}"
    return redirect(url)


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
            signup_requests = SignupRequest.query.order_by(SignupRequest.created_at.desc()).limit(30).all()
            return render_template("admin/invite_first_user.html", org=org, signup_requests=signup_requests)
        if User.query.filter_by(organization_id=org_id, email=email).first():
            flash("A user with that email already exists for this organization.", "error")
            signup_requests = SignupRequest.query.order_by(SignupRequest.created_at.desc()).limit(30).all()
            return render_template("admin/invite_first_user.html", org=org, signup_requests=signup_requests)
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
            title = "Set up your Training Tracker account"
            body = (
                f"Welcome to {org.name}!\n\n"
                f"You've been set up as a manager. Click the link below to set your password, then sign in at {org.subdomain}.{base}.\n\n"
                f"{set_password_url}\n\n"
                f"This link expires in 7 days."
            )
            send_notification_email(user, title, body, category='invite')
            flash(f"Invitation sent to {email}. They can set their password and then log in at {org.subdomain}.{base}.", "success")
            return redirect(url_for("admin.organization_detail", org_id=org_id))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "error")
            signup_requests = SignupRequest.query.order_by(SignupRequest.created_at.desc()).limit(30).all()
            return render_template("admin/invite_first_user.html", org=org, signup_requests=signup_requests)

    signup_requests = SignupRequest.query.order_by(SignupRequest.created_at.desc()).limit(30).all()
    return render_template("admin/invite_first_user.html", org=org, signup_requests=signup_requests)


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
