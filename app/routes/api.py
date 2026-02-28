from flask import Blueprint, request, jsonify, current_app, g
from itsdangerous import URLSafeTimedSerializer
from datetime import datetime, timezone
from app.models import User, Organization
from app import db

api_bp = Blueprint("api", __name__, url_prefix="/api")

def generate_api_token(user_id, expires_in=86400*30):
    """Generates a secure, 30-day API token using Flask's built-in serializer."""
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    return s.dumps({'user_id': user_id})

def verify_api_token(token, max_age=86400*30):
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        data = s.loads(token, max_age=max_age)
    except Exception:
        return None
    return data.get('user_id')

@api_bp.before_request
def api_auth():
    # Except for login route, require token
    if request.path.endswith('/login') or request.method == 'OPTIONS':
        return
        
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"error": "Missing or invalid token"}), 401
        
    token = auth_header.split(" ")[1]
    user_id = verify_api_token(token)
    if not user_id:
        return jsonify({"error": "Token expired or invalid"}), 401
        
    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 401
    
    g.api_user = user


@api_bp.post("/auth/login")
def login():
    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"error": "Missing email or password"}), 400

    # Since mobile apps don't have subdomains, look up all accounts with this email.
    users = User.query.filter_by(email=email).all()
    
    if not users:
        return jsonify({"error": "Invalid email or password"}), 401
        
    # Find the matching user by password
    valid_user = None
    for u in users:
        if u.check_password(password):
            valid_user = u
            break
            
    if not valid_user:
        return jsonify({"error": "Invalid email or password"}), 401
        
    if valid_user.password_hash is None:
        return jsonify({"error": "Account not fully set up. Please set your password via web first."}), 401
        
    token = generate_api_token(valid_user.id)
    valid_user.last_seen = datetime.now(timezone.utc)
    db.session.commit()
    
    org_name = valid_user.organization.name if valid_user.organization else "System Admin"
    
    return jsonify({
        "token": token,
        "user": {
            "id": valid_user.id,
            "email": valid_user.email,
            "first_name": valid_user.employee.first_name if valid_user.employee else "Admin",
            "last_name": valid_user.employee.last_name if valid_user.employee else "",
            "role": valid_user.role,
            "organization_name": org_name,
            "organization_id": valid_user.organization_id
        }
    })

@api_bp.get("/auth/me")
def me():
    # Token is validated in before_request
    user = g.api_user
    org_name = user.organization.name if user.organization else "System Admin"
    return jsonify({
        "user": {
            "id": user.id,
            "email": user.email,
            "first_name": user.employee.first_name if user.employee else "Admin",
            "last_name": user.employee.last_name if user.employee else "",
            "role": user.role,
            "organization_name": org_name,
            "organization_id": user.organization_id
        }
    })

# --- EMPLOYEE API ---

@api_bp.get("/employee/dashboard")
def dashboard():
    user = g.api_user
    if not user.employee_id:
        return jsonify({"error": "Account not linked to an employee"}), 400

    employee = user.employee
    # If trainee has graduated, return a flag so the app can show a different screen
    if user.role == "trainee" and getattr(employee, "graduated_at", None):
        return jsonify({"graduated": True})

    import pytz
    from sqlalchemy.orm import joinedload
    from app.models import TrainingSession, SystemSettings

    me = employee.id
    tz = pytz.timezone("US/Eastern")
    today = datetime.now(tz).date()
    oid = user.organization_id

    # Session format helper
    def format_session(s):
        return {
            "id": s.id,
            "date": s.session_date.strftime("%Y-%m-%d"),
            "position": s.position.name if s.position else "Unknown",
            "position_color": getattr(s.position, "color", "#4f46e5"),
            "trainer_name": f"{s.trainer.first_name} {s.trainer.last_name}" if s.trainer else None,
            "trainee_name": f"{s.trainee.first_name} {s.trainee.last_name}" if s.trainee else None,
            "status": "completed" if s.completed_at else ("future" if s.session_date > today else "today"),
            "score": s.rating_scale_used if s.completed_at else None,  # (Can calculate actual score if needed later)
            "is_locked": s.is_locked,
            "is_future": s.is_future
        }

    session_opts = (joinedload("position"), joinedload("trainer"), joinedload("trainee"))

    # 1. MY UPCOMING
    my_upcoming_q = TrainingSession.query.filter_by(organization_id=oid).filter(
        TrainingSession.completed_at == None,
        (TrainingSession.trainee_employee_id == me) | (TrainingSession.trainer_employee_id == me)
    ).options(*session_opts).order_by(TrainingSession.session_date.asc(), TrainingSession.id.asc()).all()

    # 2. FLOOR COVERAGE (Trainers only)
    floor_coverage_q = []
    if user.role in ["trainer", "manager"]:
        from datetime import timedelta
        cutoff = today - timedelta(days=30)
        floor_coverage_q = TrainingSession.query.filter_by(organization_id=oid).filter(
            TrainingSession.completed_at == None,
            TrainingSession.trainee_employee_id != me,
            TrainingSession.trainer_employee_id != me,
            TrainingSession.session_date >= cutoff
        ).options(*session_opts).order_by(TrainingSession.session_date.asc()).limit(50).all()

    # 3. HISTORY
    recent_sessions_q = TrainingSession.query.filter_by(organization_id=oid).filter(
        TrainingSession.completed_at != None,
        (TrainingSession.trainee_employee_id == me) | (TrainingSession.trainer_employee_id == me)
    ).options(*session_opts).order_by(TrainingSession.completed_at.desc()).limit(5).all()

    # 4. STATS
    stats = {
        "pending_count": len(my_upcoming_q),
        "total_completed": TrainingSession.query.filter_by(organization_id=oid).filter(
            TrainingSession.completed_at != None,
            (TrainingSession.trainee_employee_id == me) | (TrainingSession.trainer_employee_id == me)
        ).count(),
        "teaching_count": TrainingSession.query.filter_by(organization_id=oid, trainer_employee_id=me, completed_at=None).count()
    }

    # Hide trainee data if global settings dictate
    if user.role == "trainee":
        sys = SystemSettings.query.filter_by(organization_id=oid).first()
        if sys and not getattr(sys, "share_trainee_data_with_trainees", True):
            my_upcoming_q = []
            recent_sessions_q = []
            stats = {"pending_count": 0, "total_completed": 0, "teaching_count": 0}

    return jsonify({
        "graduated": False,
        "my_upcoming": [format_session(s) for s in my_upcoming_q],
        "floor_coverage": [format_session(s) for s in floor_coverage_q],
        "recent_sessions": [format_session(s) for s in recent_sessions_q],
        "stats": stats
    })


# --- MESSAGING API ---

@api_bp.get("/messages")
def inbox():
    """Returns the unified inbox of DMs and Channels."""
    user = g.api_user
    if not user.employee_id:
        return jsonify({"error": "No employee profile linked to user"}), 400
        
    my_id = user.employee_id
    from app.routes.messages import _build_sidebar_data
    
    sidebar_channels, sidebar_dms, sidebar_unreads, directory = _build_sidebar_data(my_id)
    
    # Format the data for JSON
    def format_convo(c):
        return {
            "type": c.get('type'),
            "channel_type": c.get('channel_type'),
            "id": c['partner'].id if c.get('type') == 'dm_legacy' else c['channel'].id,
            "display_name": c.get('display_name'),
            "last_message": c.get('last_message'),
            "timestamp": c.get('timestamp').isoformat() if c.get('timestamp') else None,
            "unread": c.get('unread')
        }
    
    # Optional: directory structure if we want to allow starting new DMs from the native app
    def format_dir(e):
        return {
            "id": e.id,
            "name": f"{e.first_name} {e.last_name}",
            "role": e.role
        }
        
    return jsonify({
        "conversations": [format_convo(c) for c in sidebar_dms + sidebar_channels],
        "directory": [format_dir(e) for e in directory]
    })


@api_bp.route("/messages/dm/<int:partner_id>", methods=["GET", "POST"])
def chat_dm(partner_id):
    """Fetch DM messages or send a new DM."""
    user = g.api_user
    if not user.employee_id:
        return jsonify({"error": "No employee profile"}), 400
        
    my_id = user.employee_id
    from app.models import Employee, Message, User
    from app.routes.messages import mark_as_read, enrich_messages_with_sessions, get_reply_to_map
    from app.utils.notifications import notify
    from sqlalchemy import or_, and_
    
    partner = Employee.query.filter_by(organization_id=user.organization_id, id=partner_id).first()
    if not partner:
        return jsonify({"error": "Partner not found"}), 404
        
    # Send message
    if request.method == "POST":
        data = request.get_json() or {}
        body = (data.get("body") or "").strip()
        if not body:
            return jsonify({"error": "Message body required"}), 400
            
        new_msg = Message(
            sender_id=my_id,
            recipient_id=partner_id,
            body=body,
            reply_to_id=data.get("reply_to_id")
        )
        db.session.add(new_msg)
        db.session.commit()
        
        # Email notification fallback
        sender_name = f"{user.employee.first_name} {user.employee.last_name}" if user.employee else "Someone"
        recipient_user = User.query.filter_by(organization_id=user.organization_id, employee_id=partner_id).first()
        if recipient_user:
            snippet = (body[:80] + '...') if len(body) > 80 else body
            try:
                from flask import url_for
                # For email, the link would ideally go to the web app
                notify(
                    recipient_user,
                    f"New message from {sender_name}",
                    snippet,
                    category='message',
                    email_only=True
                )
                db.session.commit()
            except Exception:
                pass
                
        return jsonify({"status": "success", "message_id": new_msg.id})
        
    # Get messages
    # Automatically mark as read if reader_allows
    reader_allows = getattr(user.settings, 'allow_read_receipts', True) if user.settings else True
    mark_as_read(partner_id, my_id, reader_allow_read_receipts=reader_allows)
    
    messages = Message.query.filter(
        or_(
            and_(Message.sender_id == my_id, Message.recipient_id == partner_id),
            and_(Message.sender_id == partner_id, Message.recipient_id == my_id),
        )
    ).order_by(Message.timestamp.desc()).limit(50).all() # Limit to last 50 for mobile efficiency initially
    
    # We want chronological order for display, but descending limit for fetching the *latest* 50
    messages.reverse()
    
    session_map = enrich_messages_with_sessions(messages)
    
    def format_msg(m):
        session_data = None
        match = re.search(r'\[SESSION_LINK:(\d+)\]', m.body)
        if match:
            s_id = int(match.group(1))
            sess = session_map.get(s_id)
            if sess:
                session_data = {
                    "id": sess.id,
                    "position": sess.position.name if sess.position else "Unknown",
                    "date": sess.session_date.strftime("%Y-%m-%d"),
                    "status": "completed" if sess.completed_at else "upcoming"
                }

        parsed_body = re.sub(r'\s*\[SESSION_LINK:\d+\].*', '', m.body).strip()
                
        return {
            "id": m.id,
            "sender_id": m.sender_id,
            "recipient_id": m.recipient_id,
            "body": parsed_body,
            "timestamp": m.timestamp.isoformat(),
            "read_at": m.read_at.isoformat() if m.read_at else None,
            "session_data": session_data,
            "is_me": m.sender_id == my_id
        }
        
    return jsonify({
        "partner": {
            "id": partner.id,
            "name": f"{partner.first_name} {partner.last_name}",
            "role": partner.role
        },
        "messages": [format_msg(m) for m in messages]
    })


@api_bp.post("/push-token")
def register_push_token_api():
    """Register Expo Push Token via API."""
    user = g.api_user
    data = request.get_json() or {}
    token = (data.get("token") or "").strip()
    platform = (data.get("platform") or "expo").lower()[:20]
    
    if not token:
        return jsonify({"ok": True, "registered": False})
        
    from app.models import PushToken
    existing = PushToken.query.filter_by(user_id=user.id, token=token).first()
    if existing:
        existing.platform = platform
        existing.updated_at = datetime.now(timezone.utc)
    else:
        db.session.add(PushToken(user_id=user.id, token=token, platform=platform))
        
    db.session.commit()
    return jsonify({"ok": True})



@api_bp.get("/schedules")
def schedules_list():
    user = g.api_user
    import pytz
    from datetime import timedelta
    from app.models import Schedule

    tz = pytz.timezone("US/Eastern")
    today = datetime.now(tz).date()
    me = user.employee_id
    current_week_start = today - timedelta(days=today.weekday())
    oid = user.organization_id

    if user.role == "manager":
        schedules = Schedule.query.filter_by(organization_id=oid).order_by(Schedule.start_date.asc()).all()
    else:
        schedules = Schedule.query.filter_by(organization_id=oid, status="published").order_by(Schedule.start_date.asc()).all()

    schedule_by_week_start = {s.start_date: s for s in schedules}
    enriched = []

    def format_mini_session(s):
        return {
            "id": s.id,
            "position": s.position.name if s.position else "Unknown",
            "position_color": getattr(s.position, "color", "#4f46e5"),
            "trainer_name": f"{s.trainer.first_name} {s.trainer.last_name}" if s.trainer else None,
            "trainee_name": f"{s.trainee.first_name} {s.trainee.last_name}" if s.trainee else None,
            "is_locked": s.is_locked,
            "completed": s.completed_at is not None
        }

    for s in schedules:
        week_start = s.start_date
        is_current = week_start <= today <= s.end_date
        is_past = s.end_date < today
        days = []
        for i in range(7):
            day_date = week_start + timedelta(days=i)
            day_sessions = [sess for sess in s.sessions if sess.session_date == day_date]
            if user.role != "manager":
                day_sessions = [sess for sess in day_sessions if sess.trainee_employee_id == me or sess.trainer_employee_id == me]
            days.append({
                "date": day_date.strftime("%Y-%m-%d"),
                "is_today": day_date == today,
                "session_count": len(day_sessions),
                "sessions": [format_mini_session(xs) for xs in day_sessions]
            })
        
        my_count = sum(1 for sess in s.sessions if sess.trainee_employee_id == me or sess.trainer_employee_id == me)
        enriched.append({
            "id": s.id,
            "week_start": week_start.strftime("%Y-%m-%d"),
            "week_end": s.end_date.strftime("%Y-%m-%d"),
            "my_count": my_count,
            "is_current": is_current,
            "is_past": is_past,
            "days": days
        })

    # Emulate placeholder weeks for employees so they see empty schedules incoming
    if user.role != "manager":
        for i in range(0, 7):
            week_start = current_week_start + timedelta(weeks=i)
            if week_start not in schedule_by_week_start:
                week_end = week_start + timedelta(days=6)
                days = []
                for j in range(7):
                    day_date = week_start + timedelta(days=j)
                    days.append({
                        "date": day_date.strftime("%Y-%m-%d"),
                        "is_today": day_date == today,
                        "session_count": 0,
                        "sessions": []
                    })
                enriched.append({
                    "id": None,
                    "week_start": week_start.strftime("%Y-%m-%d"),
                    "week_end": week_end.strftime("%Y-%m-%d"),
                    "my_count": 0,
                    "is_current": week_start <= today <= week_end,
                    "is_past": week_end < today,
                    "days": days
                })

    def sort_key(item):
        ws = datetime.strptime(item["week_start"], "%Y-%m-%d").date()
        if item["is_current"]: return (0, ws)
        if ws > today: return (1, ws)
        return (2, ws) # descending for past but python requires stable sort, so simplifying.

    # Sort descending past, ascending future:
    past = [x for x in enriched if x["is_past"]]
    current = [x for x in enriched if x["is_current"]]
    future = [x for x in enriched if not x["is_past"] and not x["is_current"]]
    
    past.sort(key=lambda x: x["week_start"], reverse=True)
    future.sort(key=lambda x: x["week_start"])
    final_list = current + future + past

    return jsonify({"schedules": final_list})


@api_bp.get("/schedules/<int:schedule_id>/day/<date_str>")
def schedule_day(schedule_id, date_str):
    user = g.api_user
    import pytz
    from app.models import Schedule
    
    schedule = Schedule.query.filter_by(organization_id=user.organization_id, id=schedule_id).first()
    if not schedule:
        return jsonify({"error": "Not found"}), 404

    if schedule.status != "published" and user.role == "trainee":
        return jsonify({"error": "Not published"}), 403

    try:
        day_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Invalid date"}), 400

    if not (schedule.start_date <= day_date <= schedule.end_date):
        return jsonify({"error": "Date out of bounds"}), 400

    tz = pytz.timezone("US/Eastern")
    today_in_est = datetime.now(tz).date()
    me = user.employee_id

    sessions = [s for s in schedule.sessions if s.session_date == day_date]
    if user.role != "manager":
        sessions = [s for s in sessions if s.trainee_employee_id == me or s.trainer_employee_id == me]

    def format_detailed_session(s):
        return {
            "id": s.id,
            "position": s.position.name if s.position else "Unknown",
            "position_color": getattr(s.position, "color", "#4f46e5"),
            "trainer_name": f"{s.trainer.first_name} {s.trainer.last_name}" if s.trainer else None,
            "trainee_name": f"{s.trainee.first_name} {s.trainee.last_name}" if s.trainee else None,
            "status": "completed" if s.completed_at else ("future" if s.session_date > today_in_est else "today"),
            "score": s.rating_scale_used if s.completed_at else None,
            "is_locked": s.is_locked,
            "is_future": s.is_future
        }

    return jsonify({
        "date": date_str,
        "is_today": day_date == today_in_est,
        "sessions": [format_detailed_session(s) for s in sessions]
    })

