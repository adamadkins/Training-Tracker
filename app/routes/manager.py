import os
import json
import uuid
import re
from datetime import datetime, time, timedelta, timezone, date
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, quote

import flask
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify, current_app, Response, g, session
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload
from app.models import (
    User, Employee, Position, PositionDescriptor, Schedule,
    TrainingSession, SessionRating, Notification, Daypart, EmployeeNote,
    UserSettings, SystemSettings, TrainingRoadmap, RoadmapStep, Location,
    Channel, ChannelParticipant, Message, MessageReaction, GuestTrainerToken,
    Organization, employee_locations,
)
from app import db, cache
from app.utils.notifications import send_notification_email, notify, _enqueue_or_send_push
from app.utils.schedule_parser import parse_schedule_pdf
from app.utils.hotschedules_client import fetch_weekly_shifts as hs_fetch_weekly
from app.utils.sevenshifts_client import fetch_weekly_shifts as ss_fetch_weekly, ensure_valid_token as ss_ensure_token
from app.routes.helpers import manager_required, staff_required

manager_bp = Blueprint("manager", __name__, url_prefix="/manager")


@manager_bp.before_request
def _require_tenant():
    g._manager_org_id = _org_id()
    if session.get("_spectator") and request.method not in ("GET", "HEAD", "OPTIONS"):
        from flask import flash, redirect, url_for
        flash("Read-only spectator mode. Changes are disabled.", "warning")
        return redirect(request.referrer or url_for("manager.dashboard"))


def _org_id():
    """Current tenant organization id. Abort 404 if not on a tenant subdomain."""
    oid = getattr(g, "current_organization_id", None)
    if oid is None:
        abort(404)
    return oid


def _org_has_pro():
    """True if current org has Pro plan (free or paid)."""
    oid = _org_id()
    org = Organization.query.get(oid)
    if not org:
        return False
    return (org.billing_plan == "pro") or (org.free_plan == "pro")


def _google_favicon_url(domain):
    """Return Google's favicon URL for a domain (works for any site)."""
    if not domain or "." not in domain:
        return None
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=128"


def _logo_search_domains(q):
    """Build list of domain candidates from query (slug variants, no web search)."""
    q = (q or "").strip().lower()
    if not q:
        return []
    # If it looks like a domain, use as-is (normalize)
    if "." in q and " " not in q:
        domain = q if q.startswith("http") else q.split("//")[-1].split("/")[0]
        if "." in domain:
            return [domain]
    candidates = []
    raw = re.sub(r"[^a-z0-9.-]", "", q) + ".com" if not q.endswith(".com") else q
    if raw and "." in raw:
        candidates.append(raw)
    slug = re.sub(r"\s+", "-", re.sub(r"[^a-z0-9\s]", "", q)).strip("-")
    if slug and slug + ".com" not in candidates:
        candidates.append(slug + ".com")
    slug_no_hyphen = slug.replace("-", "") if slug else ""
    if slug_no_hyphen and slug_no_hyphen + ".com" not in candidates:
        candidates.append(slug_no_hyphen + ".com")
    return candidates


def _clearbit_logo_exists(domain):
    """Return True if Clearbit has a logo for this domain. Tries HEAD then GET (some hosts block HEAD)."""
    url = f"https://logo.clearbit.com/{domain}"
    for method in ("HEAD", "GET"):
        try:
            req = Request(url, method=method, headers={"User-Agent": "Mozilla/5.0 (compatible; TrainingTracker/1.0)"})
            with urlopen(req, timeout=8) as r:
                if r.status == 200:
                    return True
        except (HTTPError, URLError, OSError):
            continue
    return False


def _domain_from_url(href):
    """Extract clean domain from URL (e.g. https://www.foo.com/path -> foo.com)."""
    if not href or not isinstance(href, str):
        return None
    try:
        parsed = urlparse(href if href.startswith("http") else "https://" + href)
        netloc = (parsed.netloc or "").strip().lower()
        if not netloc or netloc.startswith("."):
            return None
        # Drop www.
        if netloc.startswith("www."):
            netloc = netloc[4:]
        # Skip search engines and common non-company sites
        skip = ("duckduckgo.com", "google.com", "bing.com", "yahoo.com", "facebook.com",
                "twitter.com", "instagram.com", "linkedin.com", "youtube.com", "wikipedia.org",
                "amazon.com", "ebay.com", "yelp.com", "apple.com", "microsoft.com")
        if any(netloc == s or netloc.endswith("." + s) for s in skip):
            return None
        if "." in netloc and len(netloc) > 3:
            return netloc
    except Exception:
        pass
    return None


def _logo_domains_from_web_search(q):
    """Search the web for the company and return list of domains (DuckDuckGo then Google fallback)."""
    q = (q or "").strip()
    if not q or len(q) < 2:
        return []
    domains = []
    seen = set()

    def add_domains_from_urls(urls):
        for href in (urls or []):
            d = _domain_from_url(href)
            if d and d not in seen:
                seen.add(d)
                domains.append(d)

    # 1) DuckDuckGo
    try:
        from duckduckgo_search import DDGS
        search_term = f"{q} official website"
        with DDGS() as ddgs:
            results = ddgs.text(search_term, max_results=10)
        for r in (results or []):
            href = r.get("href") or r.get("link")
            add_domains_from_urls([href] if href else [])
    except Exception:
        pass

    # 2) Google fallback if we have few or no domains
    if len(domains) < 3:
        try:
            from googlesearch import search as google_search
            search_term = f"{q} official website"
            urls = list(google_search(search_term, num_results=10, lang="en"))
            add_domains_from_urls(urls)
        except Exception:
            pass

    return domains


def _logo_search_first_url(q):
    """Find company domain via web search + slug; return (clearbit_url or None, best_domain for favicon)."""
    q = (q or "").strip()
    if not q or len(q) < 2:
        return None, None
    base = "https://logo.clearbit.com/"
    # 1) Web search first so we can look up any company by name
    domains = _logo_domains_from_web_search(q)
    # 2) Add slug-based candidates (query.com, slug.com, slugnohyphen.com)
    for d in _logo_search_domains(q):
        if d and d not in domains:
            domains.append(d)
    # 3) Try Clearbit for each domain; return first hit
    for domain in domains:
        if _clearbit_logo_exists(domain):
            return base + domain, domain
    # 4) No Clearbit logo; still return first domain so caller can use Google favicon for any company
    first = domains[0] if domains else None
    return None, first


def _download_logo_to_uploads(url):
    """Download image from URL and save to static/uploads/logos/. Returns relative path or None."""
    if not url or not url.startswith("http"):
        return None
    try:
        req = Request(url, headers={"User-Agent": "TrainingTracker/1.0"})
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
        upload_dir = os.path.join(current_app.static_folder, "uploads", "logos")
        os.makedirs(upload_dir, exist_ok=True)
        name = str(uuid.uuid4()) + ext
        path = os.path.join(upload_dir, name)
        with open(path, "wb") as f:
            f.write(data)
        return "uploads/logos/" + name
    except Exception:
        return None


def _hex_expand_3(hex3):
    """Expand #rgb to #rrggbb."""
    if not hex3 or len(hex3) != 4 or hex3[0] != "#":
        return hex3
    return "#" + hex3[1] * 2 + hex3[2] * 2 + hex3[3] * 2


def _get_manager_location_id():
    """If the current user is a manager with a location restriction, return that location_id; else None."""
    if not current_user.is_authenticated or current_user.role != "manager" or not current_user.employee_id:
        return None
    oid = _org_id()
    emp = Employee.query.filter_by(organization_id=oid, id=current_user.employee_id).first()
    return emp.location_id if emp else None


def _get_manager_location_ids():
    """All location IDs this manager is assigned to (M2M + primary). None = org-wide (no restriction)."""
    if not current_user.is_authenticated or current_user.role != "manager" or not current_user.employee_id:
        return None
    oid = _org_id()
    emp = Employee.query.filter_by(organization_id=oid, id=current_user.employee_id).first()
    if not emp:
        return None
    ids = emp.location_ids()
    return ids if ids else None


def get_visible_employee_ids(location_filter=None):
    """Return list of employee IDs the current manager can see.
    location_filter: if set, restrict to employees in that location (must be in manager's locations).
    If manager has multiple locations and location_filter is None, returns employees from any of them."""
    oid = _org_id()
    loc_ids = _get_manager_location_ids()
    if loc_ids is None:
        return None  # org-wide: no filter
    if location_filter is not None:
        try:
            location_filter = int(location_filter)
        except (TypeError, ValueError):
            location_filter = None
        if location_filter is not None and location_filter in loc_ids:
            loc_ids = [location_filter]
        else:
            location_filter = None  # invalid filter, show all manager's locations
    # Employees that have any of loc_ids (via location_id or M2M)
    subq = db.session.query(employee_locations.c.employee_id).filter(
        employee_locations.c.location_id.in_(loc_ids)
    )
    rows = Employee.query.filter(
        Employee.organization_id == oid,
        Employee.status == "active",
        db.or_(
            Employee.location_id.in_(loc_ids),
            Employee.id.in_(subq),
        ),
    ).with_entities(Employee.id).all()
    return [r[0] for r in rows]


# --- HELPERS ---
def send_invite_email(user):
    token = user.get_reset_token()
    link = url_for('auth.reset_token', token=token, _external=True)
    title = "Invitation to Training Tracker"
    body = (
        f"Welcome to the team!\n\n"
        f"You've been added to Training Tracker. To access your dashboard and start your training, "
        f"set your password by clicking the link below.\n\n"
        f"{link}\n\n"
        f"This link will expire in 7 days."
    )
    send_notification_email(user, title, body, category='invite')


# --- SETUP WIZARD ---
@manager_bp.route("/setup", methods=["GET", "POST"])
@manager_required
def setup_wizard():
    oid = _org_id()
    settings = SystemSettings.query.filter_by(organization_id=oid).first()
    if not settings:
        settings = SystemSettings(organization_id=oid)
        db.session.add(settings)
        db.session.commit()
    if request.method == "GET":
        setup_done = getattr(settings, "setup_completed", True)
        run_again = request.args.get("run") == "1"
        if setup_done and not run_again:
            return render_template("setup/complete.html")
        step = getattr(settings, "setup_step", 0)
        locations = Location.query.filter_by(organization_id=oid).order_by(Location.name).all()
        return render_template(
            "setup/wizard.html",
            step=step,
            settings=settings,
            locations=locations,
            run_again=run_again,
            business_types=[
                ("restaurant", "Restaurant / Food & Beverage"),
                ("retail", "Retail"),
                ("healthcare", "Healthcare"),
                ("hospitality", "Hospitality"),
                ("other", "Other"),
            ],
        )
    # POST: advance step
    step = int(request.form.get("step", 0))
    if step == 1:
        settings.business_name = (request.form.get("business_name") or "").strip() or None
        settings.business_type = request.form.get("business_type") or None
        settings.setup_step = 2
        db.session.commit()
        return redirect(url_for("manager.setup_wizard"))
    if step == 2:
        # Branding: primary color and logo
        raw = (request.form.get("primary_color") or "indigo").strip()
        if raw.lower() in ("indigo", "blue", "violet", "green", "amber", "rose", "sky"):
            settings.primary_color = raw.lower()
        elif raw.startswith("#") and re.match(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$", raw):
            settings.primary_color = raw if len(raw) == 7 else _hex_expand_3(raw)
        logo_url = request.form.get("custom_logo_url", "").strip() or None
        logo_fallback = request.form.get("custom_logo_fallback_url", "").strip() or None
        logo_file = request.files.get("logo_file")
        if logo_file and logo_file.filename:
            ctype = (logo_file.content_type or "").lower()
            ext = os.path.splitext(secure_filename(logo_file.filename))[1] or ".png"
            if ext.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                ext = ".png"
            allowed_ct = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/x-png", "image/pjpeg")
            if ctype in allowed_ct or ctype.startswith("image/"):
                upload_dir = os.path.join(current_app.static_folder, "uploads", "logos")
                os.makedirs(upload_dir, exist_ok=True)
                name = str(uuid.uuid4()) + ext
                path = os.path.join(upload_dir, name)
                logo_file.save(path)
                logo_url = "uploads/logos/" + name
        elif logo_url and logo_url.startswith("http"):
            local_path = _download_logo_to_uploads(logo_url)
            if not local_path and logo_fallback and logo_fallback.startswith("http"):
                local_path = _download_logo_to_uploads(logo_fallback)
            if local_path:
                logo_url = local_path
        settings.custom_logo_url = logo_url
        settings.setup_step = 3
        db.session.commit()
        return redirect(url_for("manager.setup_wizard"))
    if step == 3:
        # Add locations from form (names and optional descriptions)
        names = request.form.getlist("location_name")
        for name in names:
            name = (name or "").strip()
            if name and not Location.query.filter_by(organization_id=oid, name=name).first():
                loc = Location(organization_id=oid, name=name)
                db.session.add(loc)
        settings.setup_step = 3
        db.session.commit()
        return redirect(url_for("manager.setup_wizard"))
    if step == 4:
        settings.setup_completed = True
        settings.setup_step = 0
        db.session.commit()
        cache.delete("system_settings")
        flash("Setup complete! You can change business type, branding, and locations later in Settings.", "success")
        return redirect(url_for("manager.dashboard"))
    # step 0: just advance to step 1
    settings.setup_step = 1
    db.session.commit()
    return redirect(url_for("manager.setup_wizard"))


@manager_bp.route("/setup/skip", methods=["POST"])
@manager_required
def setup_skip():
    oid = _org_id()
    settings = SystemSettings.query.filter_by(organization_id=oid).first()
    if settings:
        settings.setup_completed = True
        settings.setup_step = 0
        db.session.commit()
        cache.delete('system_settings')
    return redirect(url_for("manager.dashboard"))


def _dashboard_cache_key():
    return f"manager_dashboard/{getattr(current_user, 'id', None)}/{request.args.get('location_id', '')}"


# --- DASHBOARD ---
@manager_bp.route("/dashboard")
@manager_required
@cache.cached(timeout=60, key_prefix=_dashboard_cache_key)
def dashboard():
    oid = _org_id()
    sys_settings = SystemSettings.query.filter_by(organization_id=oid).first()
    if sys_settings and getattr(sys_settings, "setup_completed", True) is False:
        return redirect(url_for("manager.setup_wizard"))
    location_filter = request.args.get("location_id")
    visible_ids = get_visible_employee_ids(location_filter=location_filter)
    non_graduated_ids = [r[0] for r in Employee.query.filter_by(organization_id=oid).filter(Employee.graduated_at == None).with_entities(Employee.id).all()]
    # When manager has location restrictions but no employees in those locations, visible_ids can be [].
    # Avoid passing empty lists to .in_() to prevent DB/driver issues.
    empty_visible = visible_ids is not None and len(visible_ids) == 0
    if empty_visible:
        employees = []
        sessions = []
        overdue_sessions = []
        overdue_sessions_count = 0
    elif visible_ids is not None:
        active_ids = [eid for eid in visible_ids if eid in non_graduated_ids]
        employees = Employee.query.filter_by(organization_id=oid).filter(Employee.id.in_(visible_ids), Employee.graduated_at == None).options(
            selectinload(Employee.active_roadmap).selectinload(TrainingRoadmap.steps).joinedload(RoadmapStep.position),
            selectinload(Employee.trainee_sessions).options(
                joinedload(TrainingSession.position),
                selectinload(TrainingSession.ratings),
            ),
        ).all()
        dash_cutoff = date.today() - timedelta(days=90)
        if not active_ids:
            sessions = []
            overdue_sessions = []
            overdue_sessions_count = 0
        else:
            sessions = TrainingSession.query.filter_by(organization_id=g._manager_org_id).filter(
                TrainingSession.trainee_employee_id.in_(active_ids),
                TrainingSession.session_date >= dash_cutoff,
            ).options(
                joinedload(TrainingSession.position),
                joinedload(TrainingSession.trainer),
                joinedload(TrainingSession.trainee),
            ).order_by(TrainingSession.session_date.desc()).limit(200).all()
            overdue_sessions = TrainingSession.query.filter_by(organization_id=g._manager_org_id).filter(
                TrainingSession.trainee_employee_id.in_(active_ids),
                TrainingSession.completed_at == None,
                TrainingSession.session_date < date.today(),
            ).options(
                joinedload(TrainingSession.position),
                joinedload(TrainingSession.trainer),
                joinedload(TrainingSession.trainee),
            ).order_by(TrainingSession.session_date.asc()).all()
            overdue_sessions_count = len(overdue_sessions)
    else:
        employees = Employee.query.filter_by(organization_id=oid).filter(Employee.graduated_at == None).options(
            selectinload(Employee.active_roadmap).selectinload(TrainingRoadmap.steps).joinedload(RoadmapStep.position),
            selectinload(Employee.trainee_sessions).options(
                joinedload(TrainingSession.position),
                selectinload(TrainingSession.ratings),
            ),
        ).all()
        dash_cutoff = date.today() - timedelta(days=90)
        sessions = TrainingSession.query.filter_by(organization_id=g._manager_org_id).filter(
            TrainingSession.trainee_employee_id.in_(non_graduated_ids),
            TrainingSession.session_date >= dash_cutoff,
        ).options(
            joinedload(TrainingSession.position),
            joinedload(TrainingSession.trainer),
            joinedload(TrainingSession.trainee),
        ).order_by(TrainingSession.session_date.desc()).limit(200).all()
        overdue_sessions = TrainingSession.query.filter_by(organization_id=g._manager_org_id).filter(
            TrainingSession.trainee_employee_id.in_(non_graduated_ids),
            TrainingSession.completed_at == None,
            TrainingSession.session_date < date.today(),
        ).options(
            joinedload(TrainingSession.position),
            joinedload(TrainingSession.trainer),
            joinedload(TrainingSession.trainee),
        ).order_by(TrainingSession.session_date.asc()).all()
        overdue_sessions_count = len(overdue_sessions)
    if empty_visible:
        unlinked_count = 0
    else:
        assigned_employee_ids = db.session.query(User.employee_id).filter(User.employee_id != None)
        emp_query = Employee.query.filter_by(organization_id=g._manager_org_id).filter(~Employee.id.in_(assigned_employee_ids))
        if visible_ids is not None:
            emp_query = emp_query.filter(Employee.id.in_(visible_ids))
        unlinked_count = emp_query.count()

    require_signoff = getattr(sys_settings, 'require_digital_signoff', False) if sys_settings else False

    flagged_sessions = TrainingSession.query.filter_by(organization_id=g._manager_org_id).filter(
        TrainingSession.flagged == True,
        TrainingSession.flag_cleared_at == None
    ).options(
        joinedload(TrainingSession.position),
        joinedload(TrainingSession.trainer),
        joinedload(TrainingSession.trainee),
    ).order_by(TrainingSession.completed_at.desc()).all()

    manager_loc_ids = _get_manager_location_ids()
    manager_locations = []
    if manager_loc_ids:
        manager_locations = Location.query.filter_by(organization_id=g._manager_org_id).filter(Location.id.in_(manager_loc_ids)).order_by(Location.name).all()
    try:
        current_location_filter = int(location_filter) if location_filter else None
    except (TypeError, ValueError):
        current_location_filter = None

    nudge_cutoff = datetime.utcnow() - timedelta(hours=24)

    org = Organization.query.get(oid)
    trial_days_left = None
    if org and org.trial_ends_at and not org.free_plan and not org.stripe_subscription_id:
        end = org.trial_ends_at
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        delta = (end - datetime.now(timezone.utc)).days
        trial_days_left = max(0, delta)

    return render_template(
        "manager_dashboard.html",
        user=current_user,
        employees=employees,
        sessions=sessions,
        overdue_sessions=overdue_sessions,
        overdue_sessions_count=overdue_sessions_count,
        today=date.today(),
        nudge_cutoff=nudge_cutoff,
        unlinked_count=unlinked_count,
        require_signoff=require_signoff,
        flagged_sessions=flagged_sessions,
        manager_locations=manager_locations,
        current_location_filter=current_location_filter,
        trial_days_left=trial_days_left,
        trial_plan=(org.trial_plan or 'standard') if org else 'standard',
    )


# --- BILLING (tenant: checkout + portal; return to Settings) ---
def _manager_billing_return_url():
    scheme = current_app.config.get("PREFERRED_URL_SCHEME", "https")
    host = request.host.split(":")[0]
    return f"{scheme}://{host}{url_for('manager.settings')}"


@manager_bp.route("/billing/checkout", methods=["POST"])
@manager_required
def billing_create_checkout():
    """Redirect to Stripe Checkout for current org. Plan: standard | pro."""
    import stripe
    oid = _org_id()
    org = Organization.query.get_or_404(oid)
    plan = (request.form.get("plan") or "standard").strip().lower()
    secret = current_app.config.get("STRIPE_SECRET_KEY")
    if plan == "pro":
        price_id = current_app.config.get("STRIPE_PRICE_ID_PRO")
    else:
        price_id = current_app.config.get("STRIPE_PRICE_ID_STANDARD") or current_app.config.get("STRIPE_PRICE_ID")
    if not secret or not price_id:
        flash("Billing is not configured. Contact support.", "error")
        return redirect(url_for("manager.dashboard"))
    stripe.api_key = secret
    return_url = _manager_billing_return_url()
    success_url = f"{return_url}?billing=success"
    cancel_url = f"{return_url}?billing=canceled"
    customer_email = current_user.email if current_user else None
    try:
        session_params = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "client_reference_id": str(oid),
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        if org.stripe_customer_id:
            session_params["customer"] = org.stripe_customer_id
        elif customer_email:
            session_params["customer_email"] = customer_email
        session = stripe.checkout.Session.create(**session_params)
        return redirect(session.url)
    except Exception as e:
        flash(f"Could not start checkout: {str(e)}", "error")
        return redirect(url_for("manager.dashboard"))


@manager_bp.route("/billing/portal", methods=["POST"])
@manager_required
def billing_customer_portal():
    """Redirect to Stripe Customer Portal for current org."""
    import stripe
    oid = _org_id()
    org = Organization.query.get_or_404(oid)
    if not org.stripe_customer_id:
        flash("No billing account linked. Set up billing first.", "error")
        return redirect(url_for("manager.dashboard"))
    secret = current_app.config.get("STRIPE_SECRET_KEY")
    if not secret:
        flash("Billing is not configured.", "error")
        return redirect(url_for("manager.dashboard"))
    stripe.api_key = secret
    return_url = _manager_billing_return_url()
    try:
        portal = stripe.billing_portal.Session.create(
            customer=org.stripe_customer_id,
            return_url=return_url,
        )
        return redirect(portal.url)
    except Exception as e:
        flash(f"Could not open billing portal: {str(e)}", "error")
        return redirect(url_for("manager.dashboard"))


# --- NUDGE TRAINER ---
@manager_bp.route("/sessions/<int:session_id>/nudge", methods=['POST'])
@manager_required
def nudge_trainer(session_id):
    sess = TrainingSession.query.get_or_404(session_id)

    # Block nudges for already-completed sessions
    if sess.completed_at:
        flash("That session has already been submitted.", "info")
        return redirect(url_for('manager.dashboard'))

    # Enforce 24-hour cooldown between nudges
    now = datetime.utcnow()
    if sess.last_nudged_at and (now - sess.last_nudged_at) < timedelta(hours=24):
        flash("A nudge was already sent recently. Wait 24 hours before nudging again.", "warning")
        return redirect(url_for('manager.dashboard'))

    trainer_user = User.query.filter_by(employee_id=sess.trainer_employee_id).first()
    pos_name = sess.position.name if sess.position else 'a session'
    date_str = sess.session_date.strftime('%b %d')
    trainee_name = f"{sess.trainee.first_name} {sess.trainee.last_name}" if sess.trainee else 'their trainee'
    link = url_for('employee.session_rating', session_id=sess.id, _external=True)
    if trainer_user:
        notify(
            trainer_user,
            "Reminder: Submit Training Session",
            f"Please submit your {pos_name} session with {trainee_name} from {date_str}. It has not been completed yet.",
            category='session',
            link_url=link,
        )
        sess.last_nudged_at = datetime.utcnow()
        db.session.commit()
        cache.delete(_dashboard_cache_key())
        flash(f"Nudge sent to {sess.trainer.first_name}.", "success")
    else:
        flash("Trainer has no account to notify.", "warning")
    return redirect(url_for('manager.dashboard'))


# --- NOTIFICATIONS ---
@manager_bp.route("/notifications")
@login_required
def notifications():
    user_notifications = Notification.query.filter_by(user_id=current_user.id).order_by(
        Notification.created_at.desc()).all()
    return render_template("notifications.html", notifications=user_notifications)


@manager_bp.route("/notifications/<int:notification_id>/read", methods=['POST'])
@login_required
def notification_read(notification_id):
    n = Notification.query.get_or_404(notification_id)
    if n.user_id != current_user.id: abort(403)
    n.read_at = datetime.now(timezone.utc)
    db.session.commit()
    return redirect(url_for('manager.notifications'))


@manager_bp.route("/notifications/clear", methods=['POST'])
@login_required
def notifications_clear():
    Notification.query.filter_by(user_id=current_user.id, read_at=None).update({'read_at': datetime.now(timezone.utc)})
    db.session.commit()
    flash("All alerts marked as read.")
    return redirect(url_for('manager.notifications'))


# --- ACCOUNT ---
@manager_bp.route("/change_password", methods=["POST"])
@login_required
def change_password():
    new_password = request.form.get("new_password")
    confirm_password = request.form.get("confirm_password")
    if not new_password or new_password != confirm_password:
        flash("Passwords must match and cannot be empty.", "error")
        return redirect(url_for('manager.employee_detail', employee_id=current_user.employee_id))
    if current_user.check_password(new_password):
        flash("New password cannot be the same as your current password.", "error")
        return redirect(url_for('manager.employee_detail', employee_id=current_user.employee_id))
    current_user.set_password(new_password)
    db.session.commit()
    flash("Your password has been updated successfully!", "success")
    return redirect(url_for('manager.employee_detail', employee_id=current_user.employee_id))


# --- ROADMAPS ---
@manager_bp.route('/roadmaps')
@manager_required
def roadmaps_list():
    roadmaps = TrainingRoadmap.query.filter_by(organization_id=g._manager_org_id).order_by(TrainingRoadmap.name).all()
    return render_template('roadmaps/list.html', roadmaps=roadmaps)


@manager_bp.route('/roadmaps/new', methods=['GET', 'POST'])
@manager_required
def roadmap_create():
    if request.method == 'POST':
        name = request.form.get('name')
        desc = request.form.get('description')

        new_map = TrainingRoadmap(organization_id=g._manager_org_id, name=name, description=desc)
        db.session.add(new_map)
        db.session.commit()

        return redirect(url_for('manager.roadmap_builder', roadmap_id=new_map.id))
    return render_template('roadmaps/form.html')


@manager_bp.route('/roadmaps/<int:roadmap_id>/builder', methods=['GET', 'POST'])
@manager_required
def roadmap_builder(roadmap_id):
    roadmap = TrainingRoadmap.query.filter_by(organization_id=g._manager_org_id, id=roadmap_id).first_or_404()
    all_positions = Position.query.filter_by(organization_id=g._manager_org_id, active=True).order_by(Position.name).all()

    if request.method == 'POST':
        position_id = request.form.get('position_id')
        if position_id:
            count = len(roadmap.steps)
            new_step = RoadmapStep(
                roadmap_id=roadmap.id,
                position_id=position_id,
                order_index=count + 1,
                required_sessions=3,
                min_avg_rating=4.0
            )
            db.session.add(new_step)
            db.session.commit()
            flash("Step added!", "success")
        return redirect(url_for('manager.roadmap_builder', roadmap_id=roadmap.id))

    return render_template('roadmaps/builder.html', roadmap=roadmap, positions=all_positions)


@manager_bp.route('/roadmaps/step/<int:step_id>/move/<direction>', methods=['POST'])
@manager_required
def roadmap_step_move(step_id, direction):
    step = RoadmapStep.query.get_or_404(step_id)
    roadmap = step.roadmap

    neighbor = None
    if direction == 'up':
        neighbor = RoadmapStep.query.filter_by(roadmap_id=roadmap.id, order_index=step.order_index - 1).first()
    elif direction == 'down':
        neighbor = RoadmapStep.query.filter_by(roadmap_id=roadmap.id, order_index=step.order_index + 1).first()

    if neighbor:
        step.order_index, neighbor.order_index = neighbor.order_index, step.order_index
        db.session.commit()

    return redirect(url_for('manager.roadmap_builder', roadmap_id=roadmap.id))


@manager_bp.route('/roadmaps/step/<int:step_id>/update', methods=['POST'])
@manager_required
def roadmap_step_update(step_id):
    step = RoadmapStep.query.get_or_404(step_id)
    try:
        req_sessions = request.form.get('required_sessions')
        min_rating = request.form.get('min_avg_rating')

        if req_sessions: step.required_sessions = int(req_sessions)
        if min_rating: step.min_avg_rating = float(min_rating)

        db.session.commit()
        flash("Criteria updated.", "success")
    except ValueError:
        flash("Invalid input values.", "error")

    return redirect(url_for('manager.roadmap_builder', roadmap_id=step.roadmap_id))


@manager_bp.route('/roadmaps/step/<int:step_id>/delete', methods=['POST'])
@manager_required
def roadmap_step_delete(step_id):
    step = RoadmapStep.query.get_or_404(step_id)
    roadmap_id = step.roadmap_id
    db.session.delete(step)
    db.session.commit()

    roadmap = TrainingRoadmap.query.filter_by(organization_id=g._manager_org_id, id=roadmap_id).first_or_404()
    for index, s in enumerate(sorted(roadmap.steps, key=lambda x: x.order_index)):
        s.order_index = index + 1
    db.session.commit()

    return redirect(url_for('manager.roadmap_builder', roadmap_id=roadmap_id))


@manager_bp.route('/roadmaps/<int:roadmap_id>/roster', methods=['GET', 'POST'])
@manager_required
def roadmap_roster(roadmap_id):
    roadmap = TrainingRoadmap.query.filter_by(organization_id=g._manager_org_id, id=roadmap_id).first_or_404()

    if request.method == 'POST':
        now_utc = datetime.now(timezone.utc)
        add_ids = request.form.getlist('add_employee_ids')
        for emp_id in add_ids:
            emp = Employee.query.filter_by(organization_id=g._manager_org_id, id=emp_id).first()
            if emp:
                emp.current_roadmap_id = roadmap.id
                emp.roadmap_assigned_at = now_utc
                u = User.query.filter_by(employee_id=emp.id).first()
                if u:
                    notify(u, "New Roadmap Assigned",
                           f"You've been assigned to the \"{roadmap.name}\" training roadmap.",
                           category='roadmap',
                           link_url=url_for('employee.dashboard', _external=True))

        remove_ids = request.form.getlist('remove_employee_ids')
        for emp_id in remove_ids:
            emp = Employee.query.filter_by(organization_id=g._manager_org_id, id=emp_id).first()
            if emp and emp.current_roadmap_id == roadmap.id:
                emp.current_roadmap_id = None
                emp.roadmap_assigned_at = None

        db.session.commit()
        flash(f"Roster updated for {roadmap.name}.", "success")
        return redirect(url_for('manager.roadmap_roster', roadmap_id=roadmap.id))

    enrolled = Employee.query.filter_by(organization_id=g._manager_org_id).filter(Employee.current_roadmap_id == roadmap.id, Employee.graduated_at == None).all()
    available = Employee.query.filter_by(organization_id=g._manager_org_id).filter(
        (Employee.current_roadmap_id != roadmap.id) | (Employee.current_roadmap_id == None),
        Employee.status == 'active',
        Employee.role == 'trainee',
        Employee.graduated_at == None,
    ).order_by(Employee.last_name).all()

    return render_template('roadmaps/roster.html', roadmap=roadmap, enrolled=enrolled, available=available)


@manager_bp.route('/employee/<int:employee_id>/assign_roadmap', methods=['POST'])
@manager_required
def assign_roadmap(employee_id):
    employee = Employee.query.filter_by(organization_id=g._manager_org_id, id=employee_id).first_or_404()
    roadmap_id = request.form.get('roadmap_id')

    if roadmap_id:
        employee.current_roadmap_id = roadmap_id
        employee.roadmap_assigned_at = datetime.now(timezone.utc)
        roadmap = TrainingRoadmap.query.filter_by(organization_id=g._manager_org_id, id=roadmap_id).first_or_404()
        u = User.query.filter_by(employee_id=employee.id).first()
        if u and roadmap:
            notify(u, "New Roadmap Assigned",
                   f"You've been assigned to the \"{roadmap.name}\" training roadmap.",
                   category='roadmap',
                   link_url=url_for('employee.dashboard', _external=True))
        flash(f"Roadmap assigned to {employee.first_name}.", "success")
    else:
        employee.current_roadmap_id = None
        employee.roadmap_assigned_at = None
        flash("Roadmap removed.", "info")

    db.session.commit()
    return redirect(url_for('manager.employee_detail', employee_id=employee.id))


# --- SMART SCHEDULE BUILDER ---
def _sb_data_path():
    """Return path for storing smart builder JSON for the current user."""
    sb_dir = os.path.join(current_app.instance_path, 'sb_cache')
    os.makedirs(sb_dir, exist_ok=True)
    return os.path.join(sb_dir, f'sb_{current_user.id}.json')


@manager_bp.route('/schedules/smart-builder')
@manager_required
def roadmap_smart_builder():
    if not _org_has_pro():
        flash("PDF schedule upload is a Pro feature. Upgrade in Settings → Billing to use it.", "info")
        return redirect(url_for("manager.schedules_list"))
    path = _sb_data_path()
    if os.path.exists(path):
        return redirect(url_for('manager.smart_builder_build'))
    org = Organization.query.get(g._manager_org_id)
    hs_connected = bool(org and org.hs_api_url and org.hs_api_username)
    ss_connected = bool(org and org.sevenshifts_company_id)
    return render_template('roadmaps/smart_upload.html', hs_connected=hs_connected, ss_connected=ss_connected)


@manager_bp.route('/schedules/smart-builder/build')
@manager_required
def smart_builder_build():
    path = _sb_data_path()
    if not os.path.exists(path):
        flash("No schedule data found. Please upload a PDF first.", "warning")
        return redirect(url_for('manager.roadmap_smart_builder'))
    with open(path, 'r') as f:
        data = json.load(f)

    # Build a human-readable week label, e.g. "Feb. 16 – Feb. 22"
    week_label = ''
    week_start_str = data.get('week_start', '')
    if week_start_str:
        try:
            from datetime import date as _date
            ws = _date.fromisoformat(week_start_str)
            we = ws + timedelta(days=6)
            def _fmt(d):
                return d.strftime('%b. ') + str(d.day)
            week_label = f"{_fmt(ws)} – {_fmt(we)}"
        except Exception:
            pass

    return render_template('roadmaps/smart_builder.html',
                           builder_data=data,
                           all_positions=data.get('all_positions', []),
                           trainee_stats=data.get('trainee_stats', {}),
                           week_label=week_label,
                           hide_assignments=data.get('hide_assignments', False))


@manager_bp.route('/schedules/smart-builder/clear')
@manager_required
def smart_builder_clear():
    path = _sb_data_path()
    try:
        os.remove(path)
    except OSError:
        pass
    return redirect(url_for('manager.roadmap_smart_builder'))


@manager_bp.route('/schedules/smart-builder/process', methods=['POST'])
@manager_required
def process_smart_schedule():
    """Process uploaded schedule PDF for ALL 7 days, store in session, redirect to builder. Pro only."""
    if not _org_has_pro():
        flash("PDF schedule upload is a Pro feature. Upgrade in Settings → Billing.", "info")
        return redirect(url_for("manager.schedules_list"))
    try:
        # Validate file upload
        if 'file' not in request.files:
            flash('No file uploaded.', 'danger')
            return redirect(url_for('manager.roadmap_smart_builder'))

        file = request.files['file']
        if not file or file.filename == '':
            flash('Empty file.', 'danger')
            return redirect(url_for('manager.roadmap_smart_builder'))

        if not file.filename.lower().endswith('.pdf'):
            flash('File must be a PDF.', 'danger')
            return redirect(url_for('manager.roadmap_smart_builder'))

        # Save file temporarily
        timestamp = int(datetime.now().timestamp())
        filename = f"{timestamp}_{secure_filename(file.filename)}"
        filepath = os.path.join(current_app.instance_path, filename)
        file.save(filepath)

        try:
            file_size = os.path.getsize(filepath)
        except:
            file_size = -1

        # Get all active positions for the dropdown
        positions = Position.query.filter_by(organization_id=g._manager_org_id, active=True).order_by(Position.name).all()
        all_positions = [{'id': p.id, 'name': p.name, 'location_id': p.location_id} for p in positions]

        # Build trainee stats (history + suggestion) — computed once
        active_trainees = Employee.query.filter_by(organization_id=g._manager_org_id).filter(Employee.status == 'active', Employee.role == 'trainee', Employee.graduated_at == None).all()
        all_history = TrainingSession.query.filter_by(organization_id=g._manager_org_id).filter(
            TrainingSession.trainee_employee_id.in_([t.id for t in active_trainees]),
            TrainingSession.completed_at != None
        ).all()

        history_map = {}
        for s in all_history:
            if not s.ratings:
                continue
            sess_avg = sum(r.rating_value for r in s.ratings) / len(s.ratings)
            if s.trainee_employee_id not in history_map:
                history_map[s.trainee_employee_id] = {}
            if s.position_id not in history_map[s.trainee_employee_id]:
                history_map[s.trainee_employee_id][s.position_id] = {'sum': 0, 'count': 0}
            history_map[s.trainee_employee_id][s.position_id]['sum'] += sess_avg
            history_map[s.trainee_employee_id][s.position_id]['count'] += 1

        trainee_stats = {}
        for t in active_trainees:
            t_data = {'history': [], 'suggestion': None}
            t_history = history_map.get(t.id, {})
            # Filter positions by trainee's location(s) (or global)
            t_loc_ids = t.location_ids()
            t_positions = [p for p in positions
                           if p.location_id is None or p.location_id in t_loc_ids]
            for pos in t_positions:
                if pos.id in t_history:
                    stats = t_history[pos.id]
                    final_avg = round(stats['sum'] / stats['count'], 1)
                    t_data['history'].append({
                        'id': pos.id, 'name': pos.name,
                        'avg': final_avg, 'count': stats['count']
                    })

            roadmap_data = t.get_roadmap_progress()
            if roadmap_data and roadmap_data.get('target_position'):
                t_data['suggestion'] = {
                    'id': roadmap_data['target_position'].id,
                    'name': roadmap_data['target_position'].name,
                    'reason': 'Roadmap Step'
                }
            else:
                trained_ids = [h['id'] for h in t_data['history']]
                untrained = [p for p in t_positions if p.id not in trained_ids]
                if untrained:
                    t_data['suggestion'] = {
                        'id': untrained[0].id, 'name': untrained[0].name,
                        'reason': 'New Skill'
                    }
                elif t_data['history']:
                    lowest = min(t_data['history'], key=lambda x: x['avg'])
                    t_data['suggestion'] = {
                        'id': lowest['id'], 'name': lowest['name'],
                        'reason': f"Needs Improvement ({lowest['avg']})"
                    }
            trainee_stats[t.id] = t_data

        # Parse ALL 7 days from the PDF
        week_start_str = request.form.get('week_start', '')
        hide_assignments = 'hide_assignments' in request.form

        try:
            weekly_shifts = parse_schedule_pdf(filepath, week_start_str)
        except Exception as e:
            current_app.logger.error(f"Failed to batch parse schedule: {e}")
            weekly_shifts = {str(i): [] for i in range(7)}

        days_data = {}
        for day_idx in range(7):
            day_str = str(day_idx)
            parsed_shifts = weekly_shifts.get(day_str, [])

            trainers = []
            trainees = []
            for shift in parsed_shifts:
                emp = Employee.query.filter_by(organization_id=g._manager_org_id).filter(
                    db.func.concat(Employee.first_name, ' ', Employee.last_name).ilike(f"%{shift['name']}%")
                ).first()

                if emp:
                    next_step = "General"
                    stats = trainee_stats.get(emp.id)
                    if stats and stats.get('suggestion'):
                        next_step = stats['suggestion']['name']
                    elif emp.active_roadmap:
                        prog = emp.get_roadmap_progress()
                        if prog and prog.get('target_position'):
                            next_step = prog['target_position'].name

                    data = {
                        'id': emp.id,
                        'name': f"{emp.first_name} {emp.last_name}",
                        'start_time': shift['start'],
                        'end_time': shift['end'],
                        'next_step': next_step
                    }

                    if emp.role in ['manager', 'trainer']:
                        trainers.append(data)
                    else:
                        trainees.append(data)

            days_data[day_str] = {
                'trainers': trainers,
                'trainees': trainees
            }

        # Clean up file
        try:
            os.remove(filepath)
        except:
            pass

        # Store parsed data in a per-user cache file (avoids cookie size limits)
        cache_data = {
            'days': days_data,
            'all_positions': all_positions,
            'trainee_stats': {str(k): v for k, v in trainee_stats.items()},
            'file_size': file_size,
            'file_name': file.filename,
            'week_start': week_start_str,
            'hide_assignments': hide_assignments,
        }
        with open(_sb_data_path(), 'w') as f:
            json.dump(cache_data, f)

        return redirect(url_for('manager.smart_builder_build'))

    except Exception as e:
        current_app.logger.error(f"Smart Builder error: {str(e)}", exc_info=True)
        flash(f"Failed to process schedule: {str(e)}", "danger")
        return redirect(url_for('manager.roadmap_smart_builder'))


# --- HOTSCHEDULES API INTEGRATION ---

@manager_bp.route('/settings/hotschedules/connect', methods=['POST'])
@manager_required
def hs_connect():
    """Save HotSchedules API credentials for the current org."""
    org = Organization.query.get(g._manager_org_id)
    if not org:
        flash("Organization not found.", "danger")
        return redirect(url_for('manager.roadmap_smart_builder'))

    org.hs_api_url = request.form.get('hs_api_url', '').strip()
    org.hs_api_username = request.form.get('hs_api_username', '').strip()
    org.hs_api_password = request.form.get('hs_api_password', '').strip()

    if not org.hs_api_url or not org.hs_api_username or not org.hs_api_password:
        flash("All three fields (API URL, username, password) are required.", "warning")
        return redirect(url_for('manager.roadmap_smart_builder'))

    db.session.commit()
    flash("HotSchedules API connected successfully!", "success")
    return redirect(url_for('manager.roadmap_smart_builder'))


@manager_bp.route('/settings/hotschedules/disconnect', methods=['POST'])
@manager_required
def hs_disconnect():
    """Clear HotSchedules API credentials for the current org."""
    org = Organization.query.get(g._manager_org_id)
    if org:
        org.hs_api_url = None
        org.hs_api_username = None
        org.hs_api_password = None
        db.session.commit()
    flash("HotSchedules API disconnected.", "info")
    return redirect(url_for('manager.roadmap_smart_builder'))


@manager_bp.route('/schedules/smart-builder/fetch-api', methods=['POST'])
@manager_required
def smart_builder_fetch_api():
    """Pull schedule from HotSchedules API and feed into Smart Builder pipeline."""
    if not _org_has_pro():
        flash("This is a Pro feature.", "info")
        return redirect(url_for("manager.schedules_list"))

    org = Organization.query.get(g._manager_org_id)
    if not org or not org.hs_api_url or not org.hs_api_username:
        flash("HotSchedules is not connected. Please enter your API credentials first.", "warning")
        return redirect(url_for('manager.roadmap_smart_builder'))

    week_start_str = request.form.get('week_start', '')
    hide_assignments = 'hide_assignments' in request.form

    try:
        weekly_shifts = hs_fetch_weekly(
            org.hs_api_url, org.hs_api_username, org.hs_api_password, week_start_str
        )
    except Exception as e:
        current_app.logger.error(f"HotSchedules API fetch error: {e}", exc_info=True)
        flash(f"Failed to fetch from HotSchedules: {str(e)}", "danger")
        return redirect(url_for('manager.roadmap_smart_builder'))

    # --- Reuse the same employee-matching + cache logic as PDF upload ---
    positions = Position.query.filter_by(organization_id=g._manager_org_id, active=True).order_by(Position.name).all()
    all_positions = [{'id': p.id, 'name': p.name, 'location_id': p.location_id} for p in positions]

    active_trainees = Employee.query.filter_by(organization_id=g._manager_org_id).filter(
        Employee.status == 'active', Employee.role == 'trainee', Employee.graduated_at == None
    ).all()
    all_history = TrainingSession.query.filter_by(organization_id=g._manager_org_id).filter(
        TrainingSession.trainee_employee_id.in_([t.id for t in active_trainees]),
        TrainingSession.completed_at != None
    ).all()

    history_map = {}
    for s in all_history:
        if not s.ratings:
            continue
        sess_avg = sum(r.rating_value for r in s.ratings) / len(s.ratings)
        if s.trainee_employee_id not in history_map:
            history_map[s.trainee_employee_id] = {}
        if s.position_id not in history_map[s.trainee_employee_id]:
            history_map[s.trainee_employee_id][s.position_id] = {'sum': 0, 'count': 0}
        history_map[s.trainee_employee_id][s.position_id]['sum'] += sess_avg
        history_map[s.trainee_employee_id][s.position_id]['count'] += 1

    trainee_stats = {}
    for t in active_trainees:
        t_data = {'history': [], 'suggestion': None}
        t_history = history_map.get(t.id, {})
        t_loc_ids = t.location_ids()
        t_positions = [p for p in positions if p.location_id is None or p.location_id in t_loc_ids]
        for pos in t_positions:
            if pos.id in t_history:
                stats = t_history[pos.id]
                final_avg = round(stats['sum'] / stats['count'], 1)
                t_data['history'].append({'id': pos.id, 'name': pos.name, 'avg': final_avg, 'count': stats['count']})

        roadmap_data = t.get_roadmap_progress()
        if roadmap_data and roadmap_data.get('target_position'):
            t_data['suggestion'] = {'id': roadmap_data['target_position'].id, 'name': roadmap_data['target_position'].name, 'reason': 'Roadmap Step'}
        else:
            trained_ids = [h['id'] for h in t_data['history']]
            untrained = [p for p in t_positions if p.id not in trained_ids]
            if untrained:
                t_data['suggestion'] = {'id': untrained[0].id, 'name': untrained[0].name, 'reason': 'New Skill'}
            elif t_data['history']:
                lowest = min(t_data['history'], key=lambda x: x['avg'])
                t_data['suggestion'] = {'id': lowest['id'], 'name': lowest['name'], 'reason': f"Needs Improvement ({lowest['avg']})"}
        trainee_stats[t.id] = t_data

    days_data = {}
    for day_idx in range(7):
        day_str = str(day_idx)
        parsed_shifts = weekly_shifts.get(day_str, [])
        trainers = []
        trainees = []
        for shift in parsed_shifts:
            emp = Employee.query.filter_by(organization_id=g._manager_org_id).filter(
                db.func.concat(Employee.first_name, ' ', Employee.last_name).ilike(f"%{shift['name']}%")
            ).first()
            if emp:
                next_step = "General"
                stats = trainee_stats.get(emp.id)
                if stats and stats.get('suggestion'):
                    next_step = stats['suggestion']['name']
                elif emp.active_roadmap:
                    prog = emp.get_roadmap_progress()
                    if prog and prog.get('target_position'):
                        next_step = prog['target_position'].name
                data = {
                    'id': emp.id,
                    'name': f"{emp.first_name} {emp.last_name}",
                    'start_time': shift['start'],
                    'end_time': shift['end'],
                    'next_step': next_step
                }
                if emp.role in ['manager', 'trainer']:
                    trainers.append(data)
                else:
                    trainees.append(data)
        days_data[day_str] = {'trainers': trainers, 'trainees': trainees}

    cache_data = {
        'days': days_data,
        'all_positions': all_positions,
        'trainee_stats': {str(k): v for k, v in trainee_stats.items()},
        'file_size': 0,
        'file_name': 'HotSchedules API',
        'week_start': week_start_str,
        'hide_assignments': hide_assignments,
    }
    with open(_sb_data_path(), 'w') as f:
        json.dump(cache_data, f)

    return redirect(url_for('manager.smart_builder_build'))


# --- 7SHIFTS API INTEGRATION (OAuth) ---

@manager_bp.route('/auth/7shifts/callback')
@manager_required
def ss_callback():
    """OAuth callback: 7Shifts redirects here after the client authorizes.
    The callback URL receives ?company_id=...&guid=... query params."""
    company_id = request.args.get('company_id') or request.args.get('guid', '').strip()
    if not company_id:
        flash("7Shifts authorization failed — no company ID received.", "danger")
        return redirect(url_for('manager.roadmap_smart_builder'))

    org = Organization.query.get(g._manager_org_id)
    if not org:
        flash("Organization not found.", "danger")
        return redirect(url_for('manager.roadmap_smart_builder'))

    org.sevenshifts_company_id = company_id
    # Pre-fetch an access token so the connection is immediately usable
    try:
        ss_ensure_token(org, db.session)
        flash("7Shifts connected successfully!", "success")
    except Exception as e:
        current_app.logger.error(f"7Shifts token fetch failed: {e}", exc_info=True)
        flash(f"7Shifts authorized but token fetch failed: {e}", "warning")
        db.session.commit()

    return redirect(url_for('manager.roadmap_smart_builder'))


@manager_bp.route('/settings/sevenshifts/disconnect', methods=['POST'])
@manager_required
def ss_disconnect():
    """Clear 7Shifts tokens for the current org."""
    org = Organization.query.get(g._manager_org_id)
    if org:
        org.sevenshifts_company_id = None
        org.sevenshifts_access_token = None
        org.sevenshifts_refresh_token = None
        org.sevenshifts_token_expires_at = None
        db.session.commit()
    flash("7Shifts disconnected.", "info")
    return redirect(url_for('manager.roadmap_smart_builder'))


@manager_bp.route('/schedules/smart-builder/fetch-7shifts', methods=['POST'])
@manager_required
def smart_builder_fetch_7shifts():
    """Pull schedule from 7Shifts API and feed into Smart Builder pipeline."""
    if not _org_has_pro():
        flash("This is a Pro feature.", "info")
        return redirect(url_for("manager.schedules_list"))

    org = Organization.query.get(g._manager_org_id)
    if not org or not org.sevenshifts_company_id:
        flash("7Shifts is not connected. Click Connect 7Shifts first.", "warning")
        return redirect(url_for('manager.roadmap_smart_builder'))

    week_start_str = request.form.get('week_start', '')
    hide_assignments = 'hide_assignments' in request.form

    try:
        weekly_shifts = ss_fetch_weekly(org, db.session, week_start_str)
    except Exception as e:
        current_app.logger.error(f"7Shifts API fetch error: {e}", exc_info=True)
        flash(f"Failed to fetch from 7Shifts: {str(e)}", "danger")
        return redirect(url_for('manager.roadmap_smart_builder'))

    # --- Reuse the same employee-matching + cache logic ---
    positions = Position.query.filter_by(organization_id=g._manager_org_id, active=True).order_by(Position.name).all()
    all_positions = [{'id': p.id, 'name': p.name, 'location_id': p.location_id} for p in positions]

    active_trainees = Employee.query.filter_by(organization_id=g._manager_org_id).filter(
        Employee.status == 'active', Employee.role == 'trainee', Employee.graduated_at == None
    ).all()
    all_history = TrainingSession.query.filter_by(organization_id=g._manager_org_id).filter(
        TrainingSession.trainee_employee_id.in_([t.id for t in active_trainees]),
        TrainingSession.completed_at != None
    ).all()

    history_map = {}
    for s in all_history:
        if not s.ratings:
            continue
        sess_avg = sum(r.rating_value for r in s.ratings) / len(s.ratings)
        if s.trainee_employee_id not in history_map:
            history_map[s.trainee_employee_id] = {}
        if s.position_id not in history_map[s.trainee_employee_id]:
            history_map[s.trainee_employee_id][s.position_id] = {'sum': 0, 'count': 0}
        history_map[s.trainee_employee_id][s.position_id]['sum'] += sess_avg
        history_map[s.trainee_employee_id][s.position_id]['count'] += 1

    trainee_stats = {}
    for t in active_trainees:
        t_data = {'history': [], 'suggestion': None}
        t_history = history_map.get(t.id, {})
        t_loc_ids = t.location_ids()
        t_positions = [p for p in positions if p.location_id is None or p.location_id in t_loc_ids]
        for pos in t_positions:
            if pos.id in t_history:
                stats = t_history[pos.id]
                final_avg = round(stats['sum'] / stats['count'], 1)
                t_data['history'].append({'id': pos.id, 'name': pos.name, 'avg': final_avg, 'count': stats['count']})

        roadmap_data = t.get_roadmap_progress()
        if roadmap_data and roadmap_data.get('target_position'):
            t_data['suggestion'] = {'id': roadmap_data['target_position'].id, 'name': roadmap_data['target_position'].name, 'reason': 'Roadmap Step'}
        else:
            trained_ids = [h['id'] for h in t_data['history']]
            untrained = [p for p in t_positions if p.id not in trained_ids]
            if untrained:
                t_data['suggestion'] = {'id': untrained[0].id, 'name': untrained[0].name, 'reason': 'New Skill'}
            elif t_data['history']:
                lowest = min(t_data['history'], key=lambda x: x['avg'])
                t_data['suggestion'] = {'id': lowest['id'], 'name': lowest['name'], 'reason': f"Needs Improvement ({lowest['avg']})"}
        trainee_stats[t.id] = t_data

    days_data = {}
    for day_idx in range(7):
        day_str = str(day_idx)
        parsed_shifts = weekly_shifts.get(day_str, [])
        trainers = []
        trainees = []
        for shift in parsed_shifts:
            emp = Employee.query.filter_by(organization_id=g._manager_org_id).filter(
                db.func.concat(Employee.first_name, ' ', Employee.last_name).ilike(f"%{shift['name']}%")
            ).first()
            if emp:
                next_step = "General"
                stats = trainee_stats.get(emp.id)
                if stats and stats.get('suggestion'):
                    next_step = stats['suggestion']['name']
                elif emp.active_roadmap:
                    prog = emp.get_roadmap_progress()
                    if prog and prog.get('target_position'):
                        next_step = prog['target_position'].name
                data = {
                    'id': emp.id,
                    'name': f"{emp.first_name} {emp.last_name}",
                    'start_time': shift['start'],
                    'end_time': shift['end'],
                    'next_step': next_step
                }
                if emp.role in ['manager', 'trainer']:
                    trainers.append(data)
                else:
                    trainees.append(data)
        days_data[day_str] = {'trainers': trainers, 'trainees': trainees}

    cache_data = {
        'days': days_data,
        'all_positions': all_positions,
        'trainee_stats': {str(k): v for k, v in trainee_stats.items()},
        'file_size': 0,
        'file_name': '7Shifts API',
        'week_start': week_start_str,
        'hide_assignments': hide_assignments,
    }
    with open(_sb_data_path(), 'w') as f:
        json.dump(cache_data, f)

    return redirect(url_for('manager.smart_builder_build'))

@manager_bp.route('/schedules/smart-builder/create-session', methods=['POST'])
@manager_required
def create_smart_session():
    """Creates a training session from the Smart Builder interface"""
    try:
        data = request.get_json()

        trainee_id = data.get('trainee_id')
        trainer_id = data.get('trainer_id')
        position_id = data.get('position_id')
        session_date_str = data.get('session_date')
        start_time_str = data.get('start_time')
        end_time_str = data.get('end_time')
        hide_assignments = bool(data.get('hide_assignments', False))

        # Validation
        if not all([trainee_id, trainer_id, position_id, session_date_str]):
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400

        # Check if trainee and trainer exist
        trainee = Employee.query.filter_by(organization_id=g._manager_org_id, id=trainee_id).first_or_404()
        trainer = Employee.query.filter_by(organization_id=g._manager_org_id, id=trainer_id).first_or_404()
        position = Position.query.filter_by(organization_id=g._manager_org_id, id=position_id).first_or_404()

        if not trainee or not trainer or not position:
            return jsonify({'success': False, 'error': 'Invalid trainee, trainer, or position'}), 400

        # Parse date
        session_date = datetime.strptime(session_date_str, '%Y-%m-%d').date()

        # Parse times if provided
        custom_start = None
        custom_end = None
        if start_time_str and start_time_str != 'No overlap':
            try:
                custom_start = datetime.strptime(start_time_str.strip(), '%I:%M %p').time()
            except ValueError:
                pass

        if end_time_str and end_time_str != 'No overlap':
            try:
                custom_end = datetime.strptime(end_time_str.strip(), '%I:%M %p').time()
            except ValueError:
                pass

        # Create or find a schedule for this date
        schedule = Schedule.query.filter_by(organization_id=g._manager_org_id).filter(
            Schedule.start_date <= session_date,
            Schedule.end_date >= session_date
        ).first()

        if not schedule:
            # Create a new schedule for this week
            days_to_sunday = (session_date.weekday() + 1) % 7
            start_of_week = session_date - timedelta(days=days_to_sunday)
            end_of_week = start_of_week + timedelta(days=6)

            schedule = Schedule(
                organization_id=g._manager_org_id,
                start_date=start_of_week,
                end_date=end_of_week,
                status='draft',
                hide_assignments=hide_assignments,
                created_by_user_id=current_user.id
            )
            db.session.add(schedule)
            db.session.flush()

        # Check if session already exists
        existing = TrainingSession.query.filter_by(
            schedule_id=schedule.id,
            session_date=session_date,
            trainer_employee_id=trainer_id,
            trainee_employee_id=trainee_id,
            position_id=position_id
        ).first()

        if existing:
            return jsonify({
                'success': False,
                'error': 'This session already exists'
            }), 400

        # Create the session
        new_session = TrainingSession(
            organization_id=g._manager_org_id,
            schedule_id=schedule.id,
            session_date=session_date,
            daypart_id=None,
            custom_start_time=custom_start,
            custom_end_time=custom_end,
            position_id=position_id,
            trainer_employee_id=trainer_id,
            trainee_employee_id=trainee_id,
            overall_notes=f"Created via Smart Builder"
        )

        db.session.add(new_session)
        db.session.commit()

        # In-app notifications only (no email) — emails are saved for schedule publish
        sess_link = url_for('employee.session_rating', session_id=new_session.id, _external=True)
        pos_name = position.name
        sess_date_str = new_session.session_date.strftime('%b %d')
        u_trainee = User.query.filter_by(employee_id=trainee_id).first()
        if u_trainee:
            notify(u_trainee, "New Training Session",
                   f"You have a new training session for {pos_name} on {sess_date_str}.",
                   category='session', link_url=sess_link, send_email=False)
        u_trainer = User.query.filter_by(employee_id=trainer_id).first()
        if u_trainer:
            notify(u_trainer, "New Training Assignment",
                   f"You've been assigned to train {pos_name} on {sess_date_str}.",
                   category='session', link_url=sess_link, send_email=False)
        db.session.commit()

        return jsonify({
            'success': True,
            'session_id': new_session.id,
            'schedule_id': schedule.id
        })

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Smart Builder create session error: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# --- EMPLOYEES ---
@manager_bp.route("/employees")
@manager_required
def employees_list():
    visible_ids = get_visible_employee_ids()
    if visible_ids is not None:
        employees = Employee.query.filter_by(organization_id=g._manager_org_id).filter(Employee.id.in_(visible_ids), Employee.graduated_at == None).all()
    else:
        employees = Employee.query.filter_by(organization_id=g._manager_org_id).filter(Employee.graduated_at == None).all()
    return render_template("employees_list.html", employees=employees)


@manager_bp.route("/employees/new", methods=['GET', 'POST'])
@manager_required
def employee_create():
    locations = Location.query.filter_by(organization_id=g._manager_org_id).order_by(Location.name).all()
    if request.method == 'POST':
        first_name = request.form.get("first_name")
        last_name = request.form.get("last_name")
        email = request.form.get("email", "").strip().lower()
        role = request.form.get("role")
        start_date_str = request.form.get("start_date")
        location_id = request.form.get("location_id") or None
        if location_id:
            location_id = int(location_id) if location_id.isdigit() else None

        # Email is required for managers/trainers (they need to log in), optional for trainees
        if not first_name or not last_name or not role:
            flash("First name, last name, and role are required.")
            return redirect(url_for('manager.employee_create'))
        if role in ('manager', 'trainer') and not email:
            flash("Email is required for managers and trainers.")
            return redirect(url_for('manager.employee_create'))
        if email and User.query.filter_by(email=email).first():
            flash("A user with that email already exists.")
            return redirect(url_for('manager.employee_create'))

        try:
            new_emp = Employee(
                organization_id=g._manager_org_id,
                first_name=first_name,
                last_name=last_name,
                role=role,
                status='active',
                start_date=datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else None,
                location_id=location_id,
            )
            db.session.add(new_emp)
            db.session.flush()

            if email:
                new_user = User(
                    organization_id=g._manager_org_id,
                    email=email,
                    role=role,
                    employee_id=new_emp.id,
                )
                db.session.add(new_user)
                db.session.commit()
                try:
                    send_invite_email(new_user)
                    flash(f"{first_name} has been added and an invitation email was sent.")
                except Exception as email_err:
                    current_app.logger.warning("Invite email failed for %s: %s", email, email_err)
                    flash(f"{first_name} has been added. The invitation email could not be sent; you can resend from their profile.", "warning")
            else:
                db.session.commit()
                flash(f"{first_name} has been added (no email — login access not enabled).")
            return redirect(url_for('manager.employees_list'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error creating account: {str(e)}")

    return render_template("employee_form.html", mode="new", employee=None, locations=locations)


@manager_bp.route("/employees/<int:employee_id>/edit", methods=['GET', 'POST'])
@manager_required
def employee_edit(employee_id):
    visible_ids = get_visible_employee_ids()
    if visible_ids is not None and employee_id not in visible_ids:
        abort(403)
    emp = Employee.query.filter_by(organization_id=g._manager_org_id, id=employee_id).first_or_404()
    locations = Location.query.filter_by(organization_id=g._manager_org_id).order_by(Location.name).all()
    if request.method == 'POST':
        old_role = emp.role
        emp.first_name = request.form.get("first_name")
        emp.last_name = request.form.get("last_name")
        emp.role = request.form.get("role")
        start_date_str = request.form.get("start_date")
        emp.start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else None
        location_id = request.form.get("location_id") or None
        emp.location_id = int(location_id) if location_id and location_id.isdigit() else None

        user_account = User.query.filter_by(employee_id=emp.id).first()
        if user_account:
            user_account.role = emp.role

        # Notify on role change
        if old_role != emp.role and user_account:
            notify(user_account, "Your Role Has Been Updated",
                   f"Your role has been changed from {old_role.capitalize()} to {emp.role.capitalize()}.",
                   category='employee',
                   link_url=url_for('employee.dashboard', _external=True))

        db.session.commit()
        flash("Employee updated successfully.")
        return redirect(url_for('manager.employees_list'))
    return render_template("employee_form.html", mode="edit", employee=emp, locations=locations)


@manager_bp.route("/employees/<int:employee_id>/delete", methods=['POST'])
@manager_required
def employee_delete(employee_id):
    visible_ids = get_visible_employee_ids()
    if visible_ids is not None and employee_id not in visible_ids:
        abort(403)
    if employee_id == current_user.employee_id:
        flash("You cannot delete your own account.")
        return redirect(url_for('manager.employees_list'))
    emp = Employee.query.filter_by(organization_id=g._manager_org_id, id=employee_id).first_or_404()
    user_account = User.query.filter_by(organization_id=g._manager_org_id, employee_id=emp.id).first()

    # Block if employee is trainee on any session (trainee_employee_id is NOT NULL)
    if TrainingSession.query.filter_by(trainee_employee_id=emp.id).first():
        flash("Cannot delete: this employee has training sessions as trainee. Remove or reassign those sessions first.")
        return redirect(url_for('manager.employees_list'))

    try:
        if user_account:
            Notification.query.filter_by(user_id=user_account.id).delete(synchronize_session=False)
            UserSettings.query.filter_by(user_id=user_account.id).delete(synchronize_session=False)
            Schedule.query.filter_by(created_by_user_id=user_account.id).update(
                {Schedule.created_by_user_id: None}, synchronize_session=False
            )
            TrainingSession.query.filter(
                (TrainingSession.completed_by_user_id == user_account.id) |
                (TrainingSession.flag_cleared_by_user_id == user_account.id)
            ).update({
                TrainingSession.completed_by_user_id: None,
                TrainingSession.flag_cleared_by_user_id: None,
            }, synchronize_session=False)
            EmployeeNote.query.filter_by(author_user_id=user_account.id).delete(synchronize_session=False)
            db.session.delete(user_account)

        EmployeeNote.query.filter_by(trainee_employee_id=emp.id).delete(synchronize_session=False)
        MessageReaction.query.filter_by(employee_id=emp.id).delete(synchronize_session=False)
        Message.query.filter(
            (Message.sender_id == emp.id) | (Message.recipient_id == emp.id)
        ).delete(synchronize_session=False)
        ChannelParticipant.query.filter_by(employee_id=emp.id).delete(synchronize_session=False)
        GuestTrainerToken.query.filter_by(trainee_id=emp.id).delete(synchronize_session=False)
        TrainingSession.query.filter_by(trainer_employee_id=emp.id).update(
            {TrainingSession.trainer_employee_id: None}, synchronize_session=False
        )
        Channel.query.filter_by(created_by_id=emp.id).update(
            {Channel.created_by_id: None}, synchronize_session=False
        )
        db.session.execute(employee_locations.delete().where(employee_locations.c.employee_id == emp.id))
        db.session.delete(emp)
        db.session.commit()
        flash("Employee and associated login deleted.")
    except Exception as e:
        db.session.rollback()
        flash(f"Delete failed: {str(e)}", "error")
    return redirect(url_for('manager.employees_list'))


@manager_bp.route("/employees/<int:employee_id>")
@login_required
def employee_detail(employee_id):
    emp = Employee.query.filter_by(organization_id=g._manager_org_id, id=employee_id).options(
        selectinload(Employee.trainee_sessions).joinedload(TrainingSession.position),
        selectinload(Employee.trainee_sessions).selectinload(TrainingSession.ratings),
        selectinload(Employee.trainer_sessions).joinedload(TrainingSession.position),
        selectinload(Employee.trainer_sessions).joinedload(TrainingSession.trainee),
    ).first_or_404()
    is_self = (current_user.employee_id == employee_id)
    is_staff = (current_user.role in ['manager', 'trainer'])

    if current_user.role == 'trainee':
        if not is_self:
            flash("You can only view your own profile.", "info")
            return redirect(url_for('manager.employee_detail', employee_id=current_user.employee_id))
        sys = flask.g.get('system_settings') or SystemSettings.query.filter_by(organization_id=g._manager_org_id).first()
        if sys and not getattr(sys, 'share_trainee_data_with_trainees', True):
            flash("Your training data is not shared with trainees.", "info")
            return redirect(url_for('employee.dashboard'))

    if not (is_self or is_staff):
        abort(403)
    if current_user.role == 'manager':
        visible_ids = get_visible_employee_ids()
        if visible_ids is not None and employee_id not in visible_ids:
            abort(403)

    notes = EmployeeNote.query.filter_by(trainee_employee_id=emp.id).order_by(EmployeeNote.created_at.desc()).all()
    mastery_list = []
    teaching_stats = None
    admin_stats = None

    roadmaps = TrainingRoadmap.query.filter_by(organization_id=g._manager_org_id).order_by(TrainingRoadmap.name).all() if current_user.role == 'manager' else []

    if emp.role == 'trainee':
        # Active positions matching the employee's location(s) (or global) — one query for locations
        emp_loc_ids = [r[0] for r in db.session.execute(
            select(employee_locations.c.location_id).where(employee_locations.c.employee_id == emp.id)
        ).fetchall()]
        if not emp_loc_ids and getattr(emp, 'location_id', None):
            emp_loc_ids = [emp.location_id]
        pos_query = Position.query.filter_by(organization_id=g._manager_org_id, active=True)
        if emp_loc_ids:
            pos_query = pos_query.filter(
                db.or_(Position.location_id == None, Position.location_id.in_(emp_loc_ids))
            )
        active_positions = pos_query.all()
        active_pos_ids = {p.id for p in active_positions}

        # Also include any inactive positions this trainee has completed sessions for
        trained_pos_ids = {s.position_id for s in emp.trainee_sessions if s.completed_at}
        inactive_trained_ids = trained_pos_ids - active_pos_ids
        inactive_positions = Position.query.filter_by(organization_id=g._manager_org_id).filter(Position.id.in_(inactive_trained_ids)).all() if inactive_trained_ids else []

        all_positions = active_positions + inactive_positions
        for pos in all_positions:
            pos_sessions = [s for s in emp.trainee_sessions if s.position_id == pos.id and s.completed_at]
            if pos_sessions:
                all_ratings = [r.rating_value for s in pos_sessions for r in s.ratings]
                avg = round(sum(all_ratings) / len(all_ratings), 1) if all_ratings else 0
                mastery_list.append({'id': pos.id, 'name': pos.name, 'average': avg, 'session_count': len(pos_sessions),
                                     'status': 'trained', 'active': pos.active})
            else:
                mastery_list.append(
                    {'id': pos.id, 'name': pos.name, 'average': 0, 'session_count': 0, 'status': 'untrained', 'active': pos.active})

    elif emp.role == 'trainer':
        taught_sessions = [s for s in emp.trainer_sessions if s.completed_at]
        if taught_sessions:
            pos_counts = {}
            for s in taught_sessions: pos_counts[s.position.name] = pos_counts.get(s.position.name, 0) + 1
            teaching_stats = {
                'total_sessions': len(taught_sessions),
                'students_reached': len(set([s.trainee_employee_id for s in taught_sessions])),
                'top_subject': max(pos_counts, key=pos_counts.get) if pos_counts else "N/A"
            }

    elif emp.role == 'manager':
        user_id = emp.user_account.id if emp.user_account else None
        if user_id:
            schedules_created = Schedule.query.filter_by(organization_id=g._manager_org_id, created_by_user_id=user_id).count()
            notes_written = EmployeeNote.query.filter_by(author_user_id=user_id).count()
            sessions_finalized = TrainingSession.query.filter_by(completed_by_user_id=user_id).count()
            admin_stats = {'schedules': schedules_created, 'notes': notes_written, 'finalized': sessions_finalized}

    return render_template("employee_detail.html", employee=emp, notes=notes, mastery_list=mastery_list,
                           teaching_stats=teaching_stats, admin_stats=admin_stats, roadmaps=roadmaps)


@manager_bp.route("/employees/<int:employee_id>/graduate", methods=['POST'])
@manager_required
def employee_graduate(employee_id):
    visible_ids = get_visible_employee_ids()
    if visible_ids is not None and employee_id not in visible_ids:
        abort(403)
    emp = Employee.query.filter_by(organization_id=g._manager_org_id, id=employee_id).first_or_404()
    if emp.role != 'trainee':
        flash("Only trainees can be graduated.", "warning")
        return redirect(url_for('manager.employee_detail', employee_id=employee_id))
    if getattr(emp, 'graduated_at', None):
        flash("This trainee is already graduated.", "info")
        return redirect(url_for('manager.employee_detail', employee_id=employee_id))
    emp.graduated_at = datetime.now(timezone.utc)

    u = User.query.filter_by(employee_id=emp.id).first()
    if u:
        notify(u, "Congratulations, You've Graduated!",
               f"You have been graduated from your training program. Great work, {emp.first_name}!",
               category='employee',
               link_url=url_for('employee.dashboard', _external=True))

    db.session.commit()
    flash(f"{emp.first_name} {emp.last_name} has been graduated. They will see the graduation screen on next login.", "success")
    return redirect(url_for('manager.employee_detail', employee_id=employee_id))


@manager_bp.route("/employees/<int:employee_id>/position/<int:position_id>")
@login_required
def employee_position_detail(employee_id, position_id):
    if current_user.role == 'trainee' and current_user.employee_id != employee_id:
        flash("You can only view your own profile.", "info")
        return redirect(url_for('manager.employee_detail', employee_id=current_user.employee_id))
    if not (current_user.role in ['manager', 'trainer'] or current_user.employee_id == employee_id):
        abort(403)
    emp = Employee.query.filter_by(organization_id=g._manager_org_id, id=employee_id).first_or_404()
    pos = Position.query.filter_by(organization_id=g._manager_org_id, id=position_id).first_or_404()
    sessions = TrainingSession.query.filter_by(trainee_employee_id=employee_id, position_id=position_id).filter(
        TrainingSession.completed_at != None).order_by(TrainingSession.completed_at.desc()).all()
    return render_template("employee_position_detail.html", employee=emp, position=pos, sessions=sessions)


@manager_bp.route("/employees/<int:employee_id>/resend-invite", methods=['POST'])
@manager_required
def resend_invite(employee_id):
    emp = Employee.query.filter_by(organization_id=g._manager_org_id, id=employee_id).first_or_404()
    user_account = emp.user_account
    if not user_account:
        flash("No login account found.")
    elif user_account.password_hash:
        flash("This user has already completed their setup.")
    else:
        send_invite_email(user_account)
        flash(f"Invitation resent to {user_account.email}.")
    return redirect(url_for('manager.employee_detail', employee_id=emp.id))


@manager_bp.route("/employees/<int:employee_id>/notes/new", methods=['POST'])
@staff_required
def employee_add_note(employee_id):
    emp = Employee.query.filter_by(organization_id=g._manager_org_id, id=employee_id).first_or_404()
    note_text = request.form.get("note_text")
    category = request.form.get("category")
    if not note_text:
        flash("Note text is required.")
        return redirect(url_for('manager.employee_detail', employee_id=emp.id))
    new_note = EmployeeNote(trainee_employee_id=emp.id, author_user_id=current_user.id, category=category,
                            note_text=note_text)
    db.session.add(new_note)
    db.session.commit()
    flash("Internal note saved.")
    return redirect(url_for('manager.employee_detail', employee_id=emp.id))


# --- POSITIONS ---
@manager_bp.route("/positions")
@manager_required
def positions_list():
    positions = Position.query.filter_by(organization_id=g._manager_org_id).all()
    locations = Location.query.filter_by(organization_id=g._manager_org_id).order_by(Location.name).all()
    return render_template("positions_list.html", positions=positions, locations=locations)


@manager_bp.route("/positions/new", methods=['GET', 'POST'])
@manager_required
def position_create():
    if request.method == 'POST':
        name = request.form.get("name")
        descriptor_texts = request.form.getlist("descriptors[]")
        loc_id = request.form.get("location_id") or None
        if loc_id:
            loc_id = int(loc_id)
        if not name:
            flash("Position name is required.")
            return redirect(url_for('manager.position_create'))
        new_pos = Position(organization_id=g._manager_org_id, name=name, active=True, location_id=loc_id)
        db.session.add(new_pos)
        db.session.flush()
        for text in descriptor_texts:
            if text.strip(): db.session.add(PositionDescriptor(position_id=new_pos.id, text=text.strip(), active=True))
        db.session.commit()
        flash(f"Position '{name}' created.")
        return redirect(url_for('manager.positions_list'))
    locations = Location.query.filter_by(organization_id=g._manager_org_id).order_by(Location.name).all()
    return render_template("position_form.html", mode="new", position=None, locations=locations)


@manager_bp.route("/positions/<int:position_id>/edit", methods=['GET', 'POST'])
@manager_required
def position_edit(position_id):
    pos = Position.query.filter_by(organization_id=g._manager_org_id, id=position_id).first_or_404()
    if request.method == 'POST':
        pos.name = request.form.get("name")
        loc_id = request.form.get("location_id") or None
        if loc_id:
            loc_id = int(loc_id)
        pos.location_id = loc_id
        was_active = pos.active
        pos.active = 'active' in request.form

        submitted_texts = [t.strip() for t in request.form.getlist("descriptors[]") if t.strip()]
        existing = PositionDescriptor.query.filter_by(position_id=pos.id).all()
        existing_texts = {d.text for d in existing}

        # Remove descriptors no longer in the submitted list
        for d in existing:
            if d.text not in submitted_texts:
                has_ratings = db.session.query(
                    db.exists().where(SessionRating.descriptor_id == d.id)
                ).scalar()
                if has_ratings:
                    d.active = False   # preserve history, just hide from new sessions
                else:
                    db.session.delete(d)

        # Add brand-new descriptors
        for text in submitted_texts:
            if text not in existing_texts:
                db.session.add(PositionDescriptor(position_id=pos.id, text=text, active=True))

        # Re-activate any that were previously soft-deleted and are back in the list
        for d in existing:
            if d.text in submitted_texts and not d.active:
                d.active = True

        db.session.commit()

        if was_active and not pos.active:
            flash(f"Position '{pos.name}' is now inactive. It will no longer appear in new sessions or schedules, but existing history is preserved.", "warning")
        else:
            flash(f"Position '{pos.name}' updated.")
        return redirect(url_for('manager.positions_list'))

    # Build context for the deactivation warning (GET or after redirect)
    pending_sessions = TrainingSession.query.filter_by(position_id=pos.id).filter(
        TrainingSession.completed_at == None
    ).count()
    roadmap_steps = RoadmapStep.query.filter_by(position_id=pos.id).all()
    active_roadmaps = set()
    for step in roadmap_steps:
        rm = TrainingRoadmap.query.filter_by(organization_id=g._manager_org_id, id=step.roadmap_id).first()
        if rm:
            active_roadmaps.add(rm.name)
    locations = Location.query.filter_by(organization_id=g._manager_org_id).order_by(Location.name).all()
    return render_template(
        "position_form.html",
        mode="edit",
        position=pos,
        locations=locations,
        pending_sessions=pending_sessions,
        active_roadmaps=list(active_roadmaps),
    )


@manager_bp.route("/positions/<int:position_id>")
@manager_required
def position_detail(position_id):
    pos = Position.query.filter_by(organization_id=g._manager_org_id, id=position_id).first_or_404()
    descriptors = PositionDescriptor.query.filter_by(position_id=pos.id).all()
    return render_template("position_detail.html", position=pos, descriptors=descriptors)


@manager_bp.route("/positions/<int:position_id>/delete", methods=['POST'])
@manager_required
def position_delete(position_id):
    pos = Position.query.filter_by(organization_id=g._manager_org_id, id=position_id).first_or_404()
    if TrainingSession.query.filter_by(position_id=pos.id).first():
        flash("Cannot delete a position that has recorded training sessions.")
        return redirect(url_for('manager.positions_list'))
    db.session.delete(pos)
    db.session.commit()
    flash("Position deleted.")
    return redirect(url_for('manager.positions_list'))


@manager_bp.route("/positions/<int:position_id>/descriptors/new", methods=['POST'])
@manager_required
def descriptor_create(position_id):
    pos = Position.query.filter_by(organization_id=g._manager_org_id, id=position_id).first_or_404()
    text = request.form.get("text")
    if not text or not text.strip():
        flash("Skill description cannot be empty.")
        return redirect(url_for('manager.position_detail', position_id=position_id))
    new_desc = PositionDescriptor(position_id=pos.id, text=text.strip(), active=True)
    db.session.add(new_desc)
    db.session.commit()
    flash(f"Added '{text.strip()}' to the {pos.name} checklist.")
    return redirect(url_for('manager.position_detail', position_id=position_id))


@manager_bp.route("/descriptors/<int:descriptor_id>/delete", methods=['POST'])
@manager_required
def descriptor_delete(descriptor_id):
    desc = PositionDescriptor.query.get_or_404(descriptor_id)
    pos_id = desc.position_id
    db.session.delete(desc)
    db.session.commit()
    flash("Skill descriptor removed.")
    return redirect(url_for('manager.position_detail', position_id=pos_id))


# --- DAYPARTS ---
@manager_bp.route("/dayparts")
@manager_required
def dayparts_list():
    dayparts = Daypart.query.filter_by(organization_id=g._manager_org_id).order_by(Daypart.start_time.asc()).all()
    return render_template("dayparts_list.html", dayparts=dayparts)


@manager_bp.route("/dayparts/new", methods=['GET', 'POST'])
@manager_required
def daypart_create():
    if request.method == 'POST':
        name = request.form.get("name")
        start_str = request.form.get("start_time")
        end_str = request.form.get("end_time")
        try:
            new_dp = Daypart(organization_id=g._manager_org_id, name=name, active=True)
            if start_str: new_dp.start_time = datetime.strptime(start_str, '%H:%M').time()
            if end_str: new_dp.end_time = datetime.strptime(end_str, '%H:%M').time()
            db.session.add(new_dp)
            db.session.commit()
            flash(f"Daypart '{name}' created.")
            return redirect(url_for('manager.dayparts_list'))
        except ValueError:
            flash("Invalid time format.")
    return render_template("daypart_form.html", mode="new", daypart=None)


@manager_bp.route("/dayparts/<int:daypart_id>/edit", methods=['GET', 'POST'])
@manager_required
def daypart_edit(daypart_id):
    dp = Daypart.query.filter_by(organization_id=g._manager_org_id, id=daypart_id).first_or_404()
    if request.method == 'POST':
        try:
            dp.name = request.form.get("name")
            start_str = request.form.get("start_time")
            end_str = request.form.get("end_time")
            if start_str: dp.start_time = datetime.strptime(start_str, '%H:%M').time()
            if end_str: dp.end_time = datetime.strptime(end_str, '%H:%M').time()
            dp.active = 'active' in request.form
            db.session.commit()
            flash("Daypart updated.")
            return redirect(url_for('manager.dayparts_list'))
        except ValueError:
            flash("Invalid time format.")
    return render_template("daypart_form.html", mode="edit", daypart=dp)


@manager_bp.route("/dayparts/<int:daypart_id>/delete", methods=['POST'])
@manager_required
def daypart_delete(daypart_id):
    dp = Daypart.query.filter_by(organization_id=g._manager_org_id, id=daypart_id).first_or_404()
    db.session.delete(dp)
    db.session.commit()
    flash("Daypart deleted.")
    return redirect(url_for('manager.dayparts_list'))


# --- FLAG REVIEW ---
@manager_bp.route("/sessions/<int:session_id>/clear-flag", methods=['POST'])
@manager_required
def clear_flag(session_id):
    session = TrainingSession.query.get_or_404(session_id)
    if not session.flagged:
        flash("This session is not flagged.", "info")
        return redirect(url_for('manager.dashboard'))
    session.flag_cleared_at = datetime.now(timezone.utc)
    session.flag_cleared_by_user_id = current_user.id
    db.session.commit()
    flash("Flag has been reviewed and cleared.", "success")
    return redirect(url_for('manager.dashboard'))


# --- LOCATIONS ---
def _can_manage_locations():
    """Only global managers (no location restriction) can create/edit/delete locations."""
    return _get_manager_location_id() is None


@manager_bp.route("/locations")
@manager_required
def locations_list():
    locations = Location.query.filter_by(organization_id=g._manager_org_id).order_by(Location.name).all()
    can_manage = _can_manage_locations()
    return render_template("locations_list.html", locations=locations, can_manage=can_manage)


@manager_bp.route("/locations/new", methods=['GET', 'POST'])
@manager_required
def location_create():
    if request.method == 'POST':
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip() or None
        if not name:
            flash("Location name is required.")
            return redirect(url_for('manager.location_create'))
        if Location.query.filter_by(organization_id=g._manager_org_id, name=name).first():
            flash(f"Location '{name}' already exists.")
            return redirect(url_for('manager.location_create'))
        loc = Location(organization_id=g._manager_org_id, name=name, description=description)
        db.session.add(loc)
        db.session.commit()
        flash(f"Location '{name}' created.")
        return redirect(url_for('manager.locations_list'))
    return render_template("location_form.html", mode="new", location=None)


@manager_bp.route("/locations/<int:location_id>/edit", methods=['GET', 'POST'])
@manager_required
def location_edit(location_id):
    loc = Location.query.filter_by(organization_id=g._manager_org_id, id=location_id).first_or_404()
    if request.method == 'POST':
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Location name is required.")
            return redirect(url_for('manager.location_edit', location_id=location_id))
        other = Location.query.filter_by(organization_id=g._manager_org_id).filter(Location.name == name, Location.id != location_id).first()
        if other:
            flash(f"Location '{name}' already exists.")
            return redirect(url_for('manager.location_edit', location_id=location_id))
        loc.name = name
        loc.description = (request.form.get("description") or "").strip() or None
        db.session.commit()
        flash("Location updated.")
        return redirect(url_for('manager.locations_list'))
    return render_template("location_form.html", mode="edit", location=loc)


@manager_bp.route("/locations/<int:location_id>/delete", methods=['POST'])
@manager_required
def location_delete(location_id):
    loc = Location.query.filter_by(organization_id=g._manager_org_id, id=location_id).first_or_404()
    if loc.employees.count() > 0:
        flash(f"Cannot delete '{loc.name}': employees are assigned. Reassign them first.")
        return redirect(url_for('manager.locations_list'))
    db.session.delete(loc)
    db.session.commit()
    flash("Location deleted.")
    return redirect(url_for('manager.locations_list'))


@manager_bp.route("/locations/<int:location_id>/roster", methods=['GET', 'POST'])
@manager_required
def location_roster(location_id):

    loc = Location.query.filter_by(organization_id=g._manager_org_id, id=location_id).first_or_404()

    if request.method == 'POST':
        add_ids = request.form.getlist('add_employee_ids')
        remove_ids = request.form.getlist('remove_employee_ids')

        for eid in add_ids:
            emp = Employee.query.filter_by(organization_id=g._manager_org_id, id=int(eid)).first()
            if not emp:
                continue
            exists = db.session.query(employee_locations).filter(
                employee_locations.c.employee_id == emp.id,
                employee_locations.c.location_id == loc.id,
            ).first()
            if not exists:
                emp.locations.append(loc)
                if not emp.location_id:
                    emp.location_id = loc.id  # primary

        for eid in remove_ids:
            emp = Employee.query.filter_by(organization_id=g._manager_org_id, id=int(eid)).first()
            if not emp:
                continue
            if loc in list(emp.locations):  # load to avoid dynamic query issues
                emp.locations.remove(loc)
                if emp.location_id == loc.id:
                    remaining = [l.id for l in emp.locations]
                    emp.location_id = remaining[0] if remaining else None

        db.session.commit()
        flash(f"Roster for {loc.name} updated.")
        return redirect(url_for('manager.location_roster', location_id=loc.id))

    # Assigned = in M2M for this location or legacy location_id
    subq = db.session.query(employee_locations.c.employee_id).filter(employee_locations.c.location_id == loc.id)
    assigned = Employee.query.filter_by(organization_id=g._manager_org_id).filter(
        Employee.status == 'active',
        db.or_(Employee.location_id == loc.id, Employee.id.in_(subq)),
    ).order_by(Employee.first_name).all()
    assigned_ids = {e.id for e in assigned}
    available_q = Employee.query.filter_by(organization_id=g._manager_org_id).filter(Employee.status == 'active')
    if assigned_ids:
        available_q = available_q.filter(~Employee.id.in_(assigned_ids))
    available = available_q.order_by(Employee.first_name).all()

    return render_template("location_roster.html", location=loc, assigned=assigned, available=available)


# --- SCHEDULES ---
@manager_bp.route("/schedules")
@manager_required
def schedules_list():
    visible_ids = get_visible_employee_ids()
    if visible_ids is not None:
        # Schedules that have at least one session with a trainee in our location
        subq = db.session.query(TrainingSession.schedule_id).filter(
            TrainingSession.trainee_employee_id.in_(visible_ids)
        ).distinct()
        schedules = Schedule.query.filter_by(organization_id=g._manager_org_id).filter(Schedule.id.in_(subq)).order_by(Schedule.created_at.desc()).all()
    else:
        schedules = Schedule.query.filter_by(organization_id=g._manager_org_id).order_by(Schedule.created_at.desc()).all()
    return render_template("schedules_list.html", schedules=schedules)


@manager_bp.route("/schedules/create", methods=['GET', 'POST'])
@manager_required
def schedule_create():
    if request.method == 'POST':
        try:
            new_sch = Schedule(
                organization_id=g._manager_org_id,
                start_date=datetime.strptime(request.form.get("start_date"), '%Y-%m-%d'),
                end_date=datetime.strptime(request.form.get("end_date"), '%Y-%m-%d'),
                status=request.form.get("status"),
                hide_assignments=('hide_assignments' in request.form),
                created_by_user_id=current_user.id
            )
            db.session.add(new_sch)
            db.session.commit()
            return redirect(url_for('manager.schedule_detail', schedule_id=new_sch.id))
        except Exception as e:
            flash(f"Error: {str(e)}")
    sys = SystemSettings.query.filter_by(organization_id=g._manager_org_id).first()
    default_hide = getattr(sys, 'default_hide_assignments', False) if sys else False
    return render_template("schedule_form.html", mode="new", schedule=None, default_hide_assignments=default_hide)


@manager_bp.route("/schedules/<int:schedule_id>/edit", methods=['GET', 'POST'])
@manager_required
def schedule_edit(schedule_id):
    sch = Schedule.query.filter_by(organization_id=g._manager_org_id, id=schedule_id).first_or_404()
    visible_ids = get_visible_employee_ids()
    if visible_ids is not None:
        has_visible = TrainingSession.query.filter_by(schedule_id=sch.id).filter(
            TrainingSession.trainee_employee_id.in_(visible_ids)
        ).first()
        if not has_visible and TrainingSession.query.filter_by(schedule_id=sch.id).first():
            abort(403)
    if request.method == 'POST':
        try:
            sch.start_date = datetime.strptime(request.form.get("start_date"), '%Y-%m-%d')
            sch.end_date = datetime.strptime(request.form.get("end_date"), '%Y-%m-%d')
            sch.status = request.form.get("status")
            sch.hide_assignments = ('hide_assignments' in request.form)
            db.session.commit()
            cache.delete_memoized(schedule_detail)
            flash("Schedule settings updated.")
            return redirect(url_for('manager.schedules_list'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}")
    return render_template("schedule_form.html", mode="edit", schedule=sch)


@manager_bp.route("/schedules/<int:schedule_id>/delete", methods=['POST'])
@manager_required
def schedule_delete(schedule_id):
    sch = Schedule.query.filter_by(organization_id=g._manager_org_id, id=schedule_id).first_or_404()
    visible_ids = get_visible_employee_ids()
    if visible_ids is not None:
        has_visible = TrainingSession.query.filter_by(schedule_id=sch.id).filter(
            TrainingSession.trainee_employee_id.in_(visible_ids)
        ).first()
        if not has_visible and TrainingSession.query.filter_by(schedule_id=sch.id).first():
            abort(403)
    db.session.delete(sch)
    db.session.commit()
    flash("Schedule deleted.")
    return redirect(url_for('manager.schedules_list'))


def _schedule_detail_cache_key():
    return f"schedule_detail/{request.view_args.get('schedule_id')}/{getattr(current_user, 'id', None)}"


@manager_bp.route("/schedules/<int:schedule_id>")
@manager_required
@cache.cached(timeout=90, key_prefix=_schedule_detail_cache_key)
def schedule_detail(schedule_id):
    sch = Schedule.query.filter_by(organization_id=g._manager_org_id, id=schedule_id).first_or_404()
    visible_ids = get_visible_employee_ids()

    # Eager-load sessions with position, trainer, trainee (avoids N+1 in template)
    session_query = TrainingSession.query.filter_by(schedule_id=sch.id).options(
        joinedload(TrainingSession.position),
        joinedload(TrainingSession.trainer),
        joinedload(TrainingSession.trainee),
    ).order_by(TrainingSession.session_date)
    if visible_ids is not None:
        session_query = session_query.filter(TrainingSession.trainee_employee_id.in_(visible_ids))
    sessions = session_query.all()

    # Eager-load employees with roadmaps+steps, trainee_sessions+ratings (Employee.locations is dynamic - cannot eager load)
    employee_query = Employee.query.filter_by(organization_id=g._manager_org_id).filter(Employee.status == 'active', Employee.graduated_at == None).options(
        selectinload(Employee.active_roadmap).selectinload(TrainingRoadmap.steps).joinedload(RoadmapStep.position),
        selectinload(Employee.trainee_sessions).selectinload(TrainingSession.ratings),
    )
    if visible_ids is not None:
        employee_query = employee_query.filter(Employee.id.in_(visible_ids))
        if not sessions and TrainingSession.query.filter_by(schedule_id=sch.id).first():
            abort(403)
    employees = employee_query.all()

    days = []
    curr = sch.start_date
    while curr <= sch.end_date:
        days.append(curr)
        curr += timedelta(days=1)

    all_active_positions = Position.query.filter_by(organization_id=g._manager_org_id, active=True).all()
    dayparts = Daypart.query.filter_by(organization_id=g._manager_org_id, active=True).all()

    # Precompute location_ids (one query; Employee.locations is dynamic so we can't eager-load it)
    employee_location_ids = {}
    if employees:
        emp_ids = [e.id for e in employees]
        rows = db.session.execute(
            select(employee_locations.c.employee_id, employee_locations.c.location_id).where(
                employee_locations.c.employee_id.in_(emp_ids)
            )
        ).fetchall()
        for eid, lid in rows:
            employee_location_ids.setdefault(eid, []).append(lid)
        for emp in employees:
            if emp.id not in employee_location_ids and getattr(emp, 'location_id', None):
                employee_location_ids[emp.id] = [emp.location_id]
            employee_location_ids.setdefault(emp.id, [])

    active_trainees = [e for e in employees if e.role == 'trainee']
    trainee_ids = [t.id for t in active_trainees]
    trainee_stats = {}
    if trainee_ids:
        # Recent history only (limit rows to avoid timeout on large DBs)
        cutoff = date.today() - timedelta(days=365)
        all_history = TrainingSession.query.filter_by(organization_id=g._manager_org_id).filter(
            TrainingSession.trainee_employee_id.in_(trainee_ids),
            TrainingSession.completed_at != None,
            TrainingSession.session_date >= cutoff,
        ).options(selectinload(TrainingSession.ratings)).order_by(TrainingSession.completed_at.desc()).limit(2000).all()

        history_map = {}
        for s in all_history:
            if not s.ratings:
                continue
            sess_avg = sum(r.rating_value for r in s.ratings) / len(s.ratings)
            if s.trainee_employee_id not in history_map:
                history_map[s.trainee_employee_id] = {}
            if s.position_id not in history_map[s.trainee_employee_id]:
                history_map[s.trainee_employee_id][s.position_id] = {'sum': 0, 'count': 0}
            history_map[s.trainee_employee_id][s.position_id]['sum'] += sess_avg
            history_map[s.trainee_employee_id][s.position_id]['count'] += 1

        for t in active_trainees:
            t_data = {'history': [], 'suggestion': None}
            t_history = history_map.get(t.id, {})
            t_loc_ids = employee_location_ids.get(t.id, [])
            t_positions = [p for p in all_active_positions
                          if p.location_id is None or p.location_id in t_loc_ids]
            for pos in t_positions:
                if pos.id in t_history:
                    stats = t_history[pos.id]
                    final_avg = round(stats['sum'] / stats['count'], 1)
                    t_data['history'].append({'id': pos.id, 'name': pos.name, 'avg': final_avg, 'count': stats['count']})

            roadmap_data = t.get_roadmap_progress()  # uses preloaded active_roadmap, steps, trainee_sessions, ratings
            if roadmap_data and roadmap_data['target_position']:
                t_data['suggestion'] = {
                    'id': roadmap_data['target_position'].id,
                    'name': roadmap_data['target_position'].name,
                    'reason': 'Roadmap Step'
                }
            else:
                trained_ids = [h['id'] for h in t_data['history']]
                untrained = [p for p in t_positions if p.id not in trained_ids]
                if untrained:
                    t_data['suggestion'] = {'id': untrained[0].id, 'name': untrained[0].name, 'reason': 'New Skill'}
                elif t_data['history']:
                    lowest = min(t_data['history'], key=lambda x: x['avg'])
                    t_data['suggestion'] = {'id': lowest['id'], 'name': lowest['name'],
                                           'reason': f"Needs Improvement ({lowest['avg']})"}
            trainee_stats[t.id] = t_data

    return render_template("schedule_detail.html", schedule=sch, days=days, positions=all_active_positions,
                           employees=employees, dayparts=dayparts, sessions=sessions, trainee_stats=trainee_stats,
                           employee_location_ids=employee_location_ids)


@manager_bp.route("/schedules/<int:schedule_id>/publish", methods=['POST'])
@manager_required
def schedule_publish(schedule_id):
    sch = Schedule.query.filter_by(organization_id=g._manager_org_id, id=schedule_id).first_or_404()
    sch.status = 'published'
    sch.published_at = datetime.now(timezone.utc)
    sessions = TrainingSession.query.filter_by(schedule_id=sch.id).all()
    # One query for all users to notify (avoid N+1)
    emp_ids = set()
    for s in sessions:
        if s.trainer_employee_id:
            emp_ids.add(s.trainer_employee_id)
        if s.trainee_employee_id:
            emp_ids.add(s.trainee_employee_id)
    users_to_notify = list(
        User.query.filter_by(organization_id=g._manager_org_id).filter(User.employee_id.in_(emp_ids))
        .options(joinedload(User.settings))
        .all()
    ) if emp_ids else []
    # Use tenant host so employees open the link on the app subdomain (where they're logged in)
    org = sch.organization or Organization.query.get(sch.organization_id)
    scheme = current_app.config.get('PREFERRED_URL_SCHEME', 'https')
    base_domain = current_app.config.get('MAIN_DOMAIN', 'trainingtracker.me')
    path = url_for('employee.schedules_list', _external=False)
    link = f"{scheme}://{org.subdomain}.{base_domain}{path}" if org else url_for('employee.schedules_list', _external=True)
    title = "New Schedule Published"
    body = f"A training schedule for {sch.start_date.strftime('%b %d')} has been published. Check your upcoming sessions."
    # Create in-app notifications in bulk (no email yet)
    for user in users_to_notify:
        settings = getattr(user, 'settings', None)
        wants_in_app = getattr(settings, 'notify_in_app', True) if settings else True
        if wants_in_app:
            db.session.add(Notification(
                user_id=user.id, title=title, body=body,
                category='schedule', link_url=link,
            ))
    db.session.commit()
    # Enqueue emails in batch (single Redis connection)
    from app.utils.notifications import batch_enqueue_emails
    email_recipients = []
    for user in users_to_notify:
        settings = getattr(user, 'settings', None)
        wants_email = getattr(settings, 'notify_email', True) if settings else True
        if wants_email and user.email:
            email_recipients.append(str(user.email))
    if email_recipients:
        batch_enqueue_emails(email_recipients, title, body, 'schedule', link)
    for user in users_to_notify:
        settings = getattr(user, 'settings', None)
        try:
            wants_push = getattr(settings, 'notify_push', True) if settings else True
        except Exception:
            wants_push = True
        if wants_push:
            try:
                _enqueue_or_send_push(user.id, title, body, link)
            except Exception:
                pass
    cache.delete(f"schedule_detail/{schedule_id}/{current_user.id}")
    flash("Schedule published and staff notified.")
    return redirect(url_for('manager.schedules_list'))


@manager_bp.route("/schedules/<int:schedule_id>/sessions/new", methods=['POST'])
@manager_required
def session_create(schedule_id):
    try:
        time_mode = request.form.get("time_mode")
        daypart_id = None
        custom_start = None
        custom_end = None
        if time_mode == 'daypart':
            daypart_id = request.form.get("daypart_id") or None
        elif time_mode == 'custom':
            s_str = request.form.get("custom_start_time")
            e_str = request.form.get("custom_end_time")
            if s_str: custom_start = datetime.strptime(s_str, '%H:%M').time()
            if e_str: custom_end = datetime.strptime(e_str, '%H:%M').time()
        new_session = TrainingSession(
            organization_id=g._manager_org_id,
            schedule_id=schedule_id,
            session_date=datetime.strptime(request.form.get("session_date"), '%Y-%m-%d').date(),
            daypart_id=daypart_id,
            custom_start_time=custom_start,
            custom_end_time=custom_end,
            position_id=request.form.get("position_id"),
            trainer_employee_id=request.form.get("trainer_employee_id"),
            trainee_employee_id=request.form.get("trainee_employee_id"),
            overall_notes=request.form.get("notes")
        )
        db.session.add(new_session)
        db.session.commit()

        # In-app notifications only (no email) — emails are saved for schedule publish
        sess_link = url_for('employee.session_rating', session_id=new_session.id, _external=True)
        position = Position.query.filter_by(organization_id=g._manager_org_id, id=new_session.position_id).first()
        pos_name = position.name if position else 'a position'
        sess_date = new_session.session_date.strftime('%b %d')
        if new_session.trainee_employee_id:
            u = User.query.filter_by(employee_id=new_session.trainee_employee_id).first()
            if u:
                notify(u, "New Training Session",
                       f"You have a new training session for {pos_name} on {sess_date}.",
                       category='session', link_url=sess_link, send_email=False)
        if new_session.trainer_employee_id:
            u = User.query.filter_by(employee_id=new_session.trainer_employee_id).first()
            if u:
                notify(u, "New Training Assignment",
                       f"You've been assigned to train {pos_name} on {sess_date}.",
                       category='session', link_url=sess_link, send_email=False)
        db.session.commit()

        cache.delete_memoized(schedule_detail)
        flash("Session added.")
    except Exception as e:
        flash(f"Error: {str(e)}")
    return redirect(url_for('manager.schedule_detail', schedule_id=schedule_id))


@manager_bp.route("/sessions/<int:session_id>/delete", methods=['POST'])
@manager_required
def session_delete(session_id):
    sess = TrainingSession.query.get_or_404(session_id)
    sch_id = sess.schedule_id
    pos_name = sess.position.name if sess.position else 'a position'
    sess_date = sess.session_date.strftime('%b %d')

    users_to_notify = set()
    if sess.trainee_employee_id:
        u = User.query.filter_by(employee_id=sess.trainee_employee_id).first()
        if u: users_to_notify.add(u)
    if sess.trainer_employee_id:
        u = User.query.filter_by(employee_id=sess.trainer_employee_id).first()
        if u: users_to_notify.add(u)

    db.session.delete(sess)
    cache.delete_memoized(schedule_detail)
    for u in users_to_notify:
        notify(u, "Training Session Cancelled",
               f"Your training session for {pos_name} on {sess_date} has been cancelled.",
               category='session', send_email=False)
    db.session.commit()
    flash("Session removed.")
    return redirect(url_for('manager.schedule_detail', schedule_id=sch_id))


@manager_bp.route("/api/logo-search", methods=["GET"])
@login_required
@manager_required
def api_logo_search():
    """Look up any company: web search + slug for domain, then Clearbit or Google favicon."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"url": None, "fallback_url": None})
    url, domain = _logo_search_first_url(q)
    fallback_url = _google_favicon_url(domain) if domain else None
    # If Clearbit had no logo, use Google favicon as the primary so we always return something
    if not url and fallback_url:
        url = fallback_url
        fallback_url = None
    elif url and domain and "logo.clearbit.com" in url:
        fallback_url = _google_favicon_url(domain)
    return jsonify({"url": url, "fallback_url": fallback_url})


def _proxy_fetch_image(url):
    """Fetch image from allowed URL; return (data, content_type) or (None, None)."""
    allowed_hosts = ("logo.clearbit.com", "www.google.com")
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None, None
        if not any(parsed.netloc == h or parsed.netloc.endswith("." + h) for h in allowed_hosts):
            return None, None
    except Exception:
        return None, None
    try:
        req = Request(url, headers={"User-Agent": "TrainingTracker/1.0"})
        with urlopen(req, timeout=8) as r:
            data = r.read()
            ctype = (r.headers.get("Content-Type") or "image/png").split(";")[0].strip()
            if data and len(data) <= 2 * 1024 * 1024:
                return data, ctype
    except (HTTPError, URLError, OSError):
        pass
    return None, None


@manager_bp.route("/api/logo-proxy", methods=["GET"])
@login_required
@manager_required
def api_logo_proxy():
    """Proxy logo image; try fallback URL if primary returns 404 (avoids ad-blocker + Clearbit sunset)."""
    raw = request.args.get("url", "").strip()
    fallback_raw = request.args.get("fallback", "").strip()
    for candidate in (raw, fallback_raw):
        if not candidate:
            continue
        data, ctype = _proxy_fetch_image(candidate)
        if data and ctype:
            return Response(data, mimetype=ctype, direct_passthrough=True)
    abort(404)


@manager_bp.route("/api/asset", methods=["GET"])
@login_required
@manager_required
def api_asset():
    """Fetch external image via server (neutral path to avoid ad-blockers). Params: u=primary URL, f=fallback URL."""
    raw = request.args.get("u", "").strip() or request.args.get("url", "").strip()
    fallback_raw = request.args.get("f", "").strip() or request.args.get("fallback", "").strip()
    for candidate in (raw, fallback_raw):
        if not candidate:
            continue
        data, ctype = _proxy_fetch_image(candidate)
        if data and ctype:
            return Response(data, mimetype=ctype, direct_passthrough=True)
    abort(404)


@manager_bp.route("/settings", methods=['GET', 'POST'])
@login_required
def settings():
    user_settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    if not user_settings:
        user_settings = UserSettings(user_id=current_user.id)
        db.session.add(user_settings)
        db.session.commit()
    system_settings = SystemSettings.query.filter_by(organization_id=g._manager_org_id).first()
    if not system_settings:
        system_settings = SystemSettings(organization_id=g._manager_org_id)
        db.session.add(system_settings)
        db.session.commit()
    if request.method == 'POST':
        user_settings.notify_email = 'notify_email' in request.form
        user_settings.notify_in_app = 'notify_in_app' in request.form
        raw_theme = (request.form.get('theme_pref') or '').strip().lower()
        user_settings.theme_pref = raw_theme if raw_theme in ('light', 'dark', 'system') else 'system'
        user_settings.show_online_status = 'show_online_status' in request.form
        user_settings.allow_read_receipts = 'allow_read_receipts' in request.form
        if current_user.role == 'manager':
            system_settings.dm_enabled = 'dm_enabled' in request.form
            system_settings.allow_trainee_to_trainee_dm = 'allow_trainee_to_trainee_dm' in request.form
            system_settings.share_trainee_data_with_trainees = 'share_trainee_data_with_trainees' in request.form
            system_settings.default_hide_assignments = 'default_hide_assignments' in request.form
            system_settings.require_completion_notes = 'require_completion_notes' in request.form
            system_settings.require_digital_signoff = 'require_digital_signoff' in request.form
            raw_scale = int(request.form.get('default_rating_scale', 5))
            system_settings.default_rating_scale = raw_scale if raw_scale in (0, 5, 10) else 5
            if 'business_type' in request.form:
                system_settings.business_type = request.form.get('business_type') or None
            if 'business_name' in request.form:
                system_settings.business_name = (request.form.get('business_name') or '').strip() or None
            if 'primary_color' in request.form:
                raw = request.form.get('primary_color', 'indigo').strip()
                if raw in ('indigo', 'blue', 'violet', 'green', 'amber', 'rose', 'sky'):
                    system_settings.primary_color = raw.lower()
                elif raw.startswith('#') and re.match(r'^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$', raw):
                    system_settings.primary_color = raw if len(raw) == 7 else _hex_expand_3(raw)
            logo_url = request.form.get('custom_logo_url', '').strip() or None
            logo_fallback = request.form.get('custom_logo_fallback_url', '').strip() or None
            logo_file = request.files.get('logo_file')
            if logo_file and logo_file.filename:
                # Accept any image/* or by extension (browsers send varying content_type e.g. image/x-png)
                ctype = (logo_file.content_type or '').lower()
                ext = os.path.splitext(secure_filename(logo_file.filename))[1] or '.png'
                if ext.lower() not in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
                    ext = '.png'
                allowed_ct = ('image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/x-png', 'image/pjpeg')
                if ctype in allowed_ct or ctype.startswith('image/'):
                    upload_dir = os.path.join(current_app.static_folder, 'uploads', 'logos')
                    os.makedirs(upload_dir, exist_ok=True)
                    name = str(uuid.uuid4()) + ext
                    path = os.path.join(upload_dir, name)
                    logo_file.save(path)
                    logo_url = 'uploads/logos/' + name
            elif logo_url and logo_url.startswith('http'):
                local_path = _download_logo_to_uploads(logo_url)
                if not local_path and logo_fallback and logo_fallback.startswith('http'):
                    local_path = _download_logo_to_uploads(logo_fallback)
                if local_path:
                    logo_url = local_path
            system_settings.custom_logo_url = logo_url
        db.session.commit()
        cache.delete("system_settings")
        flash("Settings updated successfully!", "success")
        return redirect(url_for('manager.settings'))
    share_trainee_data = getattr(system_settings, 'share_trainee_data_with_trainees', True)
    org = Organization.query.get(g._manager_org_id) if getattr(g, '_manager_org_id', None) else None
    trial_days_left = None
    if org and org.trial_ends_at and not org.free_plan and not org.stripe_subscription_id:
        end = org.trial_ends_at
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        delta = (end - datetime.now(timezone.utc)).days
        trial_days_left = max(0, delta)
    if request.args.get("billing") == "success":
        flash("Billing set up successfully.", "success")
    elif request.args.get("billing") == "canceled":
        flash("Checkout was canceled.", "info")
    return render_template(
        'settings.html',
        user_settings=user_settings,
        system_settings=system_settings,
        share_trainee_data_with_trainees=share_trainee_data,
        org=org,
        trial_days_left=trial_days_left,
    )