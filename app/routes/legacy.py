from flask import Blueprint, render_template, abort
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload

from app.models import Employee, TrainingSession, Position

legacy_bp = Blueprint('legacy', __name__, url_prefix='/legacy')


def _require_manager():
    if not current_user.is_authenticated or current_user.role != 'manager':
        abort(403)


@legacy_bp.route('/')
@login_required
def index():
    _require_manager()

    # Active trainees only (not graduated)
    trainees_raw = (
        Employee.query
        .filter_by(role='trainee', status='active')
        .filter(Employee.graduated_at == None)
        .options(
            joinedload(Employee.trainee_sessions)
            .joinedload(TrainingSession.ratings),
            joinedload(Employee.trainee_sessions)
            .joinedload(TrainingSession.position),
        )
        .order_by(Employee.first_name)
        .all()
    )

    trainees = []
    for emp in trainees_raw:
        # Group completed sessions by position
        pos_map = {}
        for sess in emp.trainee_sessions:
            if not sess.completed_at or not sess.position:
                continue
            pos_name = sess.position.name
            if pos_name not in pos_map:
                pos_map[pos_name] = []
            pos_map[pos_name].append(sess)

        positions = []
        for pos_name, sessions in sorted(pos_map.items()):
            all_ratings = [r.rating_value for s in sessions for r in s.ratings]
            if all_ratings:
                avg_rating = sum(all_ratings) / len(all_ratings)
            else:
                avg_rating = 0.0
            # Map 0-5 rating to 0-100%
            pct = round((avg_rating / 5) * 100, 1)
            positions.append({
                'name': pos_name,
                'avg_rating': round(avg_rating, 1),
                'pct': pct,
                'session_count': len(sessions),
            })

        overall_pct = round(sum(p['pct'] for p in positions) / len(positions), 1) if positions else 0.0

        trainees.append({
            'id': emp.id,
            'first_name': emp.first_name,
            'last_name': emp.last_name,
            'start_date': emp.start_date.strftime('%b %d, %Y') if emp.start_date else '—',
            'positions': positions,
            'overall_pct': overall_pct,
        })

    return render_template('legacy.html', trainees=trainees)
