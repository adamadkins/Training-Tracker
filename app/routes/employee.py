from __future__ import annotations
from datetime import datetime, date, timedelta, timezone
import flask
from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, jsonify
from flask_login import login_required, current_user
import pytz
from sqlalchemy.orm import joinedload
from app import db
from app.models import (
    Employee, TrainingSession, SessionRating, Schedule,
    Position, Daypart, PositionDescriptor, Notification, SystemSettings, User
)
from app.utils.notifications import notify

employee_bp = Blueprint('employee', __name__, url_prefix='/employee')


# --- HELPER ---
def get_current_week_start():
    """Returns the Monday of the current week."""
    today = date.today()
    return today - timedelta(days=today.weekday())


# --- DASHBOARD & NOTIFICATIONS ---
@employee_bp.route("/dashboard")
@login_required
def dashboard():
    if not current_user.employee_id:
        flash("Your account is not linked to an employee profile.", "warning")
        return redirect(url_for('auth.home'))

    employee = current_user.employee

    # Graduated trainees see the graduation screen instead of the dashboard
    if current_user.role == 'trainee' and getattr(employee, 'graduated_at', None):
        return redirect(url_for('employee.graduation'))

    me = employee.id
    tz = pytz.timezone('US/Eastern')
    today = datetime.now(tz).date()

    # 1. YOUR UPCOMING (eager load to avoid N+1 in template)
    session_opts = (joinedload(TrainingSession.position), joinedload(TrainingSession.trainer), joinedload(TrainingSession.trainee))
    my_upcoming = TrainingSession.query.filter(
        TrainingSession.completed_at == None,
        (TrainingSession.trainee_employee_id == me) | (TrainingSession.trainer_employee_id == me)
    ).options(*session_opts).order_by(TrainingSession.session_date.asc(), TrainingSession.id.asc()).all()

    # 2. FLOOR COVERAGE (trainers/managers only) — limit to recent + cap rows
    floor_coverage = []
    if current_user.role in ['trainer', 'manager']:
        coverage_cutoff = today - timedelta(days=30)
        floor_coverage = TrainingSession.query.filter(
            TrainingSession.completed_at == None,
            TrainingSession.trainee_employee_id != me,
            TrainingSession.trainer_employee_id != me,
            TrainingSession.session_date >= coverage_cutoff,
        ).options(*session_opts).order_by(TrainingSession.session_date.asc()).limit(50).all()

    # 3. HISTORY
    recent_sessions = TrainingSession.query.filter(
        TrainingSession.completed_at != None,
        (TrainingSession.trainee_employee_id == me) | (TrainingSession.trainer_employee_id == me)
    ).options(*session_opts).order_by(TrainingSession.completed_at.desc()).limit(5).all()

    # 4. STATS
    stats = {
        'pending_count': len(my_upcoming),
        'total_completed': TrainingSession.query.filter(
            TrainingSession.completed_at != None,
            (TrainingSession.trainee_employee_id == me) | (TrainingSession.trainer_employee_id == me)
        ).count(),
        'teaching_count': TrainingSession.query.filter_by(trainer_employee_id=me, completed_at=None).count()
    }

    trainee_data_hidden = False
    if current_user.role == 'trainee':
        sys = getattr(flask.g, 'system_settings', None) or SystemSettings.query.first()
        if sys and not getattr(sys, 'share_trainee_data_with_trainees', True):
            trainee_data_hidden = True
            my_upcoming = []
            recent_sessions = []
            stats = {'pending_count': 0, 'total_completed': 0, 'teaching_count': 0}

    if current_user.role == 'trainee':
        template = "trainee_dashboard.html"
    else:
        template = "trainer_dashboard.html"

    return render_template(
        template,
        employee=employee,
        my_upcoming=my_upcoming,
        floor_coverage=floor_coverage,
        recent_sessions=recent_sessions,
        stats=stats,
        today=today,
        trainee_data_hidden=trainee_data_hidden,
    )


@employee_bp.route("/graduation")
@login_required
def graduation():
    """Shown to trainees after a manager marks them as graduated. Smooth offboarding screen."""
    if not current_user.employee_id or current_user.role != 'trainee':
        return redirect(url_for('employee.dashboard'))
    employee = current_user.employee
    if not getattr(employee, 'graduated_at', None):
        return redirect(url_for('employee.dashboard'))
    return render_template("graduation.html", employee=employee)


@employee_bp.route("/sessions/<int:session_id>/take-over", methods=['POST'])
@login_required
def take_over_session(session_id):
    # Only trainers or managers can take over
    if current_user.role not in ['trainer', 'manager']:
        flash("You do not have permission to reassign sessions.", "error")
        return redirect(url_for('employee.dashboard'))

    sess = TrainingSession.query.get_or_404(session_id)

    if sess.completed_at:
        flash("This session is already finalized.", "error")
        return redirect(url_for('employee.dashboard'))

    old_trainer_id = sess.trainer_employee_id
    sess.trainer_employee_id = current_user.employee_id

    pos_name = sess.position.name if sess.position else 'a position'
    sess_link = url_for('employee.session_rating', session_id=sess.id, _external=True)
    new_trainer_name = current_user.employee.first_name + ' ' + current_user.employee.last_name

    # Notify the trainee
    if sess.trainee_employee_id:
        u = User.query.filter_by(employee_id=sess.trainee_employee_id).first()
        if u:
            notify(u, "Trainer Reassigned",
                   f"{new_trainer_name} has taken over your {pos_name} training session.",
                   category='session', link_url=sess_link)

    # Notify old trainer
    if old_trainer_id and old_trainer_id != current_user.employee_id:
        u = User.query.filter_by(employee_id=old_trainer_id).first()
        if u:
            notify(u, "Session Reassigned",
                   f"{new_trainer_name} has taken over your {pos_name} session with {sess.trainee.first_name}.",
                   category='session')

    db.session.commit()

    flash(f"Success! You have taken over training for {sess.trainee.first_name}.", "success")
    return redirect(url_for('employee.dashboard'))


@employee_bp.route("/notifications")
@login_required
def notifications():
    user_notifications = Notification.query.filter_by(user_id=current_user.id).order_by(
        Notification.created_at.desc()).all()
    return render_template("notifications.html", notifications=user_notifications)


@employee_bp.route("/notifications/<int:notification_id>/read", methods=['POST'])
@login_required
def notification_read(notification_id):
    n = Notification.query.get_or_404(notification_id)
    if n.user_id != current_user.id:
        abort(403)
    n.read_at = datetime.now(timezone.utc)
    db.session.commit()
    return redirect(url_for('employee.notifications'))


@employee_bp.route("/notifications/clear", methods=['POST'])
@login_required
def notifications_clear():
    Notification.query.filter_by(user_id=current_user.id, read_at=None).update({'read_at': datetime.now(timezone.utc)})
    db.session.commit()
    flash("All alerts marked as read.", "success")
    return redirect(url_for('employee.notifications'))


@employee_bp.route("/api/notification_count")
@login_required
def api_notification_count():
    count = Notification.query.filter_by(user_id=current_user.id, read_at=None).count()
    return jsonify({'count': count})


@employee_bp.route("/api/tutorial-seen", methods=['POST'])
@login_required
def api_tutorial_seen():
    current_user.has_seen_tutorial = True
    db.session.commit()
    return jsonify({'ok': True})


@employee_bp.route("/api/tutorial-reset", methods=['POST'])
@login_required
def api_tutorial_reset():
    current_user.has_seen_tutorial = False
    db.session.commit()
    return jsonify({'ok': True})


# --- SCHEDULES (THE WEEKLY VIEW) ---
@employee_bp.route("/schedules")
@login_required
def schedules_list():
    if current_user.role == 'manager':
        schedules = Schedule.query.order_by(Schedule.start_date.desc()).all()
    else:
        schedules = Schedule.query.filter_by(status='published').order_by(Schedule.start_date.desc()).all()

    tz = pytz.timezone('US/Eastern')
    today = datetime.now(tz).date()
    me = current_user.employee_id

    enriched = []
    for s in schedules:
        my_count = 0
        completed_count = 0
        total_count = len(s.sessions)
        for sess in s.sessions:
            if sess.trainee_employee_id == me or sess.trainer_employee_id == me:
                my_count += 1
            if sess.completed_at:
                completed_count += 1
        is_current = s.start_date <= today <= s.end_date
        is_past = s.end_date < today
        enriched.append({
            'schedule': s,
            'my_count': my_count,
            'total_count': total_count,
            'completed_count': completed_count,
            'is_current': is_current,
            'is_past': is_past,
        })

    return render_template("employee_schedules_list.html", schedules=enriched, today=today)


@employee_bp.route("/schedule/<int:schedule_id>")
@login_required
def weekly_schedule(schedule_id):
    schedule = Schedule.query.get_or_404(schedule_id)

    if schedule.status != 'published' and current_user.role == 'trainee':
        abort(403)

    tz = pytz.timezone('US/Eastern')
    today_in_est = datetime.now(tz).date()

    days = []
    total_my_sessions = 0
    learning_count = 0
    teaching_count = 0

    for i in range(7):
        current_date = schedule.start_date + timedelta(days=i)
        day_sessions = [s for s in schedule.sessions if s.session_date == current_date]

        for s in day_sessions:
            if s.trainee_employee_id == current_user.employee_id:
                total_my_sessions += 1
                learning_count += 1
            elif s.trainer_employee_id == current_user.employee_id:
                total_my_sessions += 1
                teaching_count += 1

        days.append({
            'date': current_date,
            'sessions': day_sessions,
            'is_today': current_date == today_in_est
        })

    week_stats = {
        'total': total_my_sessions,
        'learning': learning_count,
        'teaching': teaching_count
    }

    return render_template(
        "employee_schedule_detail.html",
        schedule=schedule,
        days=days,
        week_stats=week_stats
    )


# --- SESSIONS & RATINGS ---
@employee_bp.route('/sessions/<int:session_id>')
@login_required
def session_rating(session_id):
    session = TrainingSession.query.get_or_404(session_id)

    # 1. BLIND MODE CHECK (Managers bypass)
    if current_user.role != 'manager' and session.is_locked:
        flash("Assignment details are hidden until the session starts.", "info")
        return redirect(url_for('employee.weekly_schedule', schedule_id=session.schedule_id))

    # 2. FUTURE TIME CHECK
    # UPDATED: Allow Trainees to view future sessions (to see the rubric)
    # Only block Trainers from starting it early.
    is_trainee = (current_user.employee_id == session.trainee_employee_id)

    if session.is_future and not is_trainee and current_user.role != 'manager':
        flash("You cannot open this session until the scheduled start time.", "warning")
        return redirect(url_for('employee.weekly_schedule', schedule_id=session.schedule_id))

    # 3. ACCESS CHECK
    is_staff = current_user.role in ['trainer', 'manager']
    is_involved = (current_user.employee_id == session.trainee_employee_id) or \
                  (current_user.employee_id == session.trainer_employee_id)

    if not (is_staff or is_involved):
        abort(403)

    descriptors = PositionDescriptor.query.filter_by(position_id=session.position_id, active=True).all()
    history = TrainingSession.query.filter_by(
        trainee_employee_id=session.trainee_employee_id,
        position_id=session.position_id
    ).filter(TrainingSession.completed_at.isnot(None)).order_by(TrainingSession.completed_at.desc()).all()

    # Rating scale: from session (if completed) or system settings (0 = legacy sliders)
    system_settings = SystemSettings.query.first()
    default_scale = system_settings.default_rating_scale if system_settings is not None else 5
    if default_scale not in (0, 5, 10):
        default_scale = 5
    current_scale_max = 10 if default_scale == 0 else default_scale
    rating_style = "legacy" if default_scale == 0 else "numeric"
    scale_max = getattr(session, "rating_scale_used", None) or current_scale_max

    # Check if digital sign-off is required
    require_signoff = getattr(system_settings, 'require_digital_signoff', False) if system_settings else False

    # --- VIEW ROUTING ---
    # If completed OR if user is the Trainee (regardless of time), show read-only detail
    if session.completed_at or is_trainee:
        return render_template(
            "session_detail.html",
            session=session, descriptors=descriptors, history=history,
            scale_max=scale_max, require_signoff=require_signoff,
            is_trainee=is_trainee, is_manager=(current_user.role == 'manager')
        )

    require_notes = getattr(system_settings, 'require_completion_notes', False) if system_settings else False

    return render_template(
        "session_rating.html",
        session=session, descriptors=descriptors, history=history,
        scale_max=current_scale_max, rating_style=rating_style,
        require_notes=require_notes
    )


@employee_bp.route('/sessions/<int:session_id>/rate', methods=['POST'])
@login_required
def session_submit_rating(session_id):
    session = TrainingSession.query.get_or_404(session_id)

    if current_user.role not in ['trainer', 'manager']:
        flash("Only trainers or managers can submit evaluations.", "danger")
        return redirect(url_for('employee.session_rating', session_id=session_id))

    try:
        system_settings = SystemSettings.query.first()
        default_scale = system_settings.default_rating_scale if system_settings is not None else 5
        if default_scale not in (0, 5, 10):
            default_scale = 5
        max_score = 10 if default_scale == 0 else default_scale

        if session.trainer_employee_id != current_user.employee_id:
            session.trainer_employee_id = current_user.employee_id

        for key, value in request.form.items():
            if key.startswith('rating_'):
                descriptor_id = int(key.split('_')[1])
                score = int(value)
                if score < 1 or score > max_score:
                    flash(f"Rating must be between 1 and {max_score}.", "danger")
                    return redirect(url_for('employee.session_rating', session_id=session_id))

                rating = SessionRating.query.filter_by(
                    training_session_id=session.id,
                    descriptor_id=descriptor_id
                ).first()

                if not rating:
                    rating = SessionRating(training_session_id=session.id, descriptor_id=descriptor_id)
                    db.session.add(rating)

                rating.rating_value = score
                rating.comment = request.form.get(f'comment_{descriptor_id}')

        session.overall_notes = request.form.get('overall_notes')
        session.completed_at = datetime.now(timezone.utc)
        session.completed_by_user_id = current_user.id
        session.rating_scale_used = max_score

        # Handle "Needs Attention" flag
        if request.form.get('flagged'):
            session.flagged = True
            session.flag_reason = request.form.get('flag_reason', '').strip() or None
            session.flag_notes = request.form.get('flag_notes', '').strip() or None

        db.session.commit()

        # Notify trainee that their session was completed
        pos_name = session.position.name if session.position else 'a position'
        sess_link = url_for('employee.session_rating', session_id=session.id, _external=True)
        trainee_name = session.trainee.first_name + ' ' + session.trainee.last_name if session.trainee else 'a trainee'

        if session.trainee_employee_id:
            u = User.query.filter_by(employee_id=session.trainee_employee_id).first()
            if u and u.id != current_user.id:
                notify(u, "Training Session Completed",
                       f"Your training session for {pos_name} has been completed and rated.",
                       category='session', link_url=sess_link)

        # Notify one manager (only if the completer is not already a manager)
        if current_user.role != 'manager':
            mgr = User.query.filter_by(role='manager').first()
            if mgr:
                notify(mgr, "Training Session Completed",
                       f"{trainee_name} completed a {pos_name} training session.",
                       category='session', link_url=sess_link)

        # If flagged, send high-priority notification to all managers
        if session.flagged:
            reason_text = session.flag_reason or 'No category specified'
            manager_users = User.query.filter_by(role='manager').all()
            for mu in manager_users:
                if mu.id != current_user.id:
                    notify(mu, "Needs Attention: Session Flagged",
                           f"{trainee_name}'s {pos_name} session was flagged: {reason_text}",
                           category='flag', link_url=sess_link)

        db.session.commit()

        # If digital sign-off is required, redirect to acknowledgment page
        if system_settings and getattr(system_settings, 'require_digital_signoff', False):
            flash("Evaluation submitted. Please hand the device to the trainee for acknowledgment.", "info")
            return redirect(url_for('employee.session_acknowledge', session_id=session.id))

        flash("Training evaluation submitted successfully.", "success")
        return redirect(url_for('employee.dashboard'))

    except Exception as e:
        db.session.rollback()
        flash(f"Error submitting rating: {str(e)}", "danger")
        return redirect(url_for('employee.session_rating', session_id=session_id))


# --- DIGITAL SIGN-OFF ACKNOWLEDGMENT ---
@employee_bp.route('/sessions/<int:session_id>/acknowledge', methods=['GET', 'POST'])
@login_required
def session_acknowledge(session_id):
    session = TrainingSession.query.get_or_404(session_id)
    if not session.completed_at:
        flash("This session has not been evaluated yet.", "warning")
        return redirect(url_for('employee.session_rating', session_id=session_id))
    if session.acknowledged_at:
        flash("This session has already been acknowledged.", "info")
        return redirect(url_for('employee.session_rating', session_id=session_id))

    descriptors = PositionDescriptor.query.filter_by(position_id=session.position_id, active=True).all()
    scale_max = session.rating_scale_used or 5

    if request.method == 'POST':
        sig = request.form.get('signature_data', '').strip()
        if not sig:
            flash("Please provide your signature to acknowledge.", "warning")
            return redirect(url_for('employee.session_acknowledge', session_id=session_id))
        session.signature_data = sig
        session.acknowledged_at = datetime.now(timezone.utc)
        db.session.commit()
        flash("Evaluation acknowledged. Thank you!", "success")
        return redirect(url_for('employee.dashboard'))

    return render_template('session_acknowledge.html', session=session,
                           descriptors=descriptors, scale_max=scale_max)


# --- QUICK TRAIN ---
@employee_bp.route('/quick-train', methods=['GET', 'POST'])
@login_required
def quick_train():
    if current_user.role == 'trainee':
        flash("Access denied.", "danger")
        return redirect(url_for('employee.dashboard'))

    if request.method == 'POST':
        trainee_id = request.form.get('trainee_id')
        position_id = request.form.get('position_id')

        tz = pytz.timezone('US/Eastern')
        today = datetime.now(tz).date()

        current_schedule = Schedule.query.filter(
            Schedule.start_date <= today,
            Schedule.end_date >= today
        ).first()

        if not current_schedule:
            monday = today - timedelta(days=today.weekday())
            sunday = monday + timedelta(days=6)
            current_schedule = Schedule(
                start_date=monday, end_date=sunday,
                status='published', created_by_user_id=current_user.id
            )
            db.session.add(current_schedule)
            db.session.flush()

        new_session = TrainingSession(
            schedule_id=current_schedule.id,
            trainer_employee_id=current_user.employee.id,
            trainee_employee_id=trainee_id,
            position_id=position_id,
            session_date=today
        )

        db.session.add(new_session)
        db.session.commit()

        # Notify trainee about the quick session
        position = Position.query.get(position_id)
        pos_name = position.name if position else 'a position'
        sess_link = url_for('employee.session_rating', session_id=new_session.id, _external=True)
        u = User.query.filter_by(employee_id=int(trainee_id)).first()
        if u:
            notify(u, "New Training Session",
                   f"A quick training session for {pos_name} has been started with you.",
                   category='session', link_url=sess_link)
            db.session.commit()

        flash(f"Quick session started for {today.strftime('%b %d')}", "success")
        return redirect(url_for('employee.session_rating', session_id=new_session.id))

    trainees = Employee.query.filter(Employee.role == 'trainee', Employee.status == 'active', Employee.graduated_at == None).all()
    positions = Position.query.filter_by(active=True).all()

    return render_template('manager_quick_train.html', trainees=trainees, positions=positions)
