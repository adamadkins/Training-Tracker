from __future__ import annotations
from datetime import datetime, timezone, timedelta

import pytz
from flask import (Blueprint, render_template, redirect, url_for,
                   request, jsonify, abort, g)
from flask_login import login_required, current_user

from app import db
from app.models import (
    Employee, Position, PositionDescriptor, Schedule,
    SessionRating, SystemSettings, TrainingSession, User,
    GuestTrainerToken
)
from app.utils.notifications import notify

guest_bp = Blueprint('guest', __name__, url_prefix='/guest')


# ── Manager: generate a guest token ──────────────────────────────────────────

@guest_bp.route('/generate', methods=['POST'])
@login_required
def generate():
    if current_user.role != 'manager':
        return jsonify({'error': 'Forbidden'}), 403

    trainee_id = request.form.get('trainee_id', type=int)
    position_id = request.form.get('position_id', type=int)

    if not trainee_id or not position_id:
        return jsonify({'error': 'trainee_id and position_id are required'}), 400

    oid = getattr(g, 'current_organization_id', None)
    if not oid:
        return jsonify({'error': 'Organization required'}), 404
    trainee = Employee.query.filter_by(organization_id=oid, id=trainee_id).first()
    position = Position.query.filter_by(organization_id=oid, id=position_id).first()
    if not trainee or not position:
        return jsonify({'error': 'Invalid trainee or position'}), 404

    token = GuestTrainerToken(
        trainee_id=trainee_id,
        position_id=position_id,
        created_by_id=current_user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.session.add(token)
    db.session.commit()

    link = url_for('guest.enter_name', token=token.token, _external=True)
    sms_body = (
        f"Hi! You've been asked to train {trainee.first_name} {trainee.last_name} "
        f"on {position.name}. "
        f"Tap the link to get started (expires in 24 hours): {link}"
    )

    return jsonify({
        'link': link,
        'sms_body': sms_body,
        'trainee_name': f"{trainee.first_name} {trainee.last_name}",
        'position_name': position.name,
    })


# ── Guest: name entry page ────────────────────────────────────────────────────

@guest_bp.route('/train/<token>', methods=['GET', 'POST'])
def enter_name(token):
    gt = GuestTrainerToken.query.filter_by(token=token).first()

    if not gt or not gt.is_valid:
        return render_template('guest/expired.html'), 410

    if request.method == 'POST':
        name = request.form.get('guest_name', '').strip()
        if not name:
            return render_template(
                'guest/enter_name.html',
                gt=gt, error="Please enter your name."
            )
        return redirect(url_for('guest.rate_session', token=token, name=name))

    return render_template('guest/enter_name.html', gt=gt, error=None)


# ── Guest: rating form ────────────────────────────────────────────────────────

@guest_bp.route('/train/<token>/rate', methods=['GET', 'POST'])
def rate_session(token):
    gt = GuestTrainerToken.query.filter_by(token=token).first()

    if not gt or not gt.is_valid:
        return render_template('guest/expired.html'), 410

    guest_name = request.args.get('name', '').strip() or request.form.get('guest_name', '').strip()
    if not guest_name:
        return redirect(url_for('guest.enter_name', token=token))

    descriptors = PositionDescriptor.query.filter_by(
        position_id=gt.position_id, active=True
    ).all()

    oid = gt.trainee.organization_id if gt.trainee else None
    if not oid:
        abort(404)
    system_settings = SystemSettings.query.filter_by(organization_id=oid).first()
    default_scale = system_settings.default_rating_scale if system_settings else 5
    if default_scale not in (0, 5, 10):
        default_scale = 5
    scale_max = 10 if default_scale == 0 else default_scale
    rating_style = 'legacy' if default_scale == 0 else 'buttons'

    if request.method == 'POST':
        # Re-check token validity at submit time
        if not gt.is_valid:
            return render_template('guest/expired.html'), 410

        # Find or create a schedule for today
        tz = pytz.timezone('US/Eastern')
        today = datetime.now(tz).date()
        current_schedule = Schedule.query.filter_by(organization_id=oid).filter(
            Schedule.start_date <= today,
            Schedule.end_date >= today
        ).first()

        if not current_schedule:
            monday = today - timedelta(days=today.weekday())
            sunday = monday + timedelta(days=6)
            current_schedule = Schedule(
                start_date=monday, end_date=sunday,
                status='published',
                created_by_user_id=gt.created_by_id,
                organization_id=oid,
            )
            db.session.add(current_schedule)
            db.session.flush()

        # Create the training session attributed to the guest
        session = TrainingSession(
            schedule_id=current_schedule.id,
            trainer_employee_id=None,
            guest_trainer_name=guest_name,
            trainee_employee_id=gt.trainee_id,
            position_id=gt.position_id,
            session_date=today,
            organization_id=oid,
        )
        db.session.add(session)
        db.session.flush()

        # Save ratings
        for key, value in request.form.items():
            if key.startswith('rating_'):
                try:
                    descriptor_id = int(key.split('_')[1])
                    score = int(value)
                except (ValueError, IndexError):
                    continue

                if score < 1 or score > scale_max:
                    continue

                rating = SessionRating(
                    training_session_id=session.id,
                    descriptor_id=descriptor_id,
                    rating_value=score,
                    comment=request.form.get(f'comment_{descriptor_id}'),
                )
                db.session.add(rating)

        session.overall_notes = request.form.get('overall_notes')
        session.completed_at = datetime.now(timezone.utc)
        session.rating_scale_used = scale_max

        # Mark token used
        gt.used_at = datetime.now(timezone.utc)
        db.session.commit()

        # Notify the trainee
        pos_name = gt.position.name
        sess_link = url_for('employee.session_rating', session_id=session.id, _external=True)
        u = User.query.filter_by(organization_id=oid, employee_id=gt.trainee_id).first()
        if u:
            notify(u, "Training Session Completed",
                   f"Your training session for {pos_name} has been completed and rated by {guest_name}.",
                   category='session', link_url=sess_link)

        # Notify all managers
        manager_users = User.query.filter_by(organization_id=oid, role='manager').all()
        trainee = gt.trainee
        trainee_name = f"{trainee.first_name} {trainee.last_name}"
        for mgr in manager_users:
            notify(mgr, "Guest Training Session Completed",
                   f"{guest_name} (guest) completed a {pos_name} session with {trainee_name}.",
                   category='session', link_url=sess_link)

        db.session.commit()

        return redirect(url_for('guest.done',
                                trainee=gt.trainee.first_name,
                                position=pos_name))

    return render_template(
        'guest/rate.html',
        gt=gt,
        guest_name=guest_name,
        descriptors=descriptors,
        scale_max=scale_max,
        rating_style=rating_style,
    )


# ── Guest: confirmation page ──────────────────────────────────────────────────

@guest_bp.route('/done')
def done():
    trainee = request.args.get('trainee', 'the trainee')
    position = request.args.get('position', 'the position')
    return render_template('guest/done.html', trainee=trainee, position=position)
