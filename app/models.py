from __future__ import annotations
from datetime import datetime, timezone, time
import pytz
import uuid
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer as Serializer
from flask import current_app
import hashlib
from sqlalchemy import Index
from app import db


class User(db.Model, UserMixin):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=True)
    role = db.Column(db.String(20), nullable=False, default="trainee")
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)
    has_seen_tutorial = db.Column(db.Boolean, default=False)
    last_seen = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    employee = db.relationship("Employee", foreign_keys=[employee_id],
                               backref=db.backref("user_account", uselist=False))

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        if not self.password_hash: return False
        return check_password_hash(self.password_hash, password)

    def get_reset_token(self):
        s = Serializer(current_app.config['SECRET_KEY'])
        return s.dumps({'user_id': self.id})

    @staticmethod
    def verify_reset_token(token, expires_sec=1800):
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            user_id = s.loads(token, max_age=expires_sec)['user_id']
        except:
            return None
        return User.query.get(user_id)


# Many-to-many: employees can be assigned to multiple locations (e.g. cross-training, managers)
employee_locations = db.Table(
    "employee_locations",
    db.Column("employee_id", db.Integer, db.ForeignKey("employees.id"), primary_key=True),
    db.Column("location_id", db.Integer, db.ForeignKey("locations.id"), primary_key=True),
)


class Location(db.Model):
    __tablename__ = "locations"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    employees = db.relationship("Employee", backref="location", lazy="dynamic")


class Employee(db.Model):
    __tablename__ = "employees"
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="trainee")
    status = db.Column(db.String(20), nullable=False, default="active")
    start_date = db.Column(db.Date, nullable=True)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), nullable=True)
    graduated_at = db.Column(db.DateTime, nullable=True)  # When manager marked trainee as graduated (offboarding)

    current_roadmap_id = db.Column(db.Integer, db.ForeignKey("training_roadmaps.id"), nullable=True)
    roadmap_assigned_at = db.Column(db.DateTime, nullable=True)  # When the current roadmap was assigned; sessions before this date are excluded from mastery
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships - Added backref here
    active_roadmap = db.relationship("TrainingRoadmap", foreign_keys=[current_roadmap_id], backref="trainees")
    # Multiple location assignments (cross-training; managers can see multiple locations)
    locations = db.relationship(
        "Location",
        secondary=employee_locations,
        backref=db.backref("roster_employees", lazy="dynamic"),
        lazy="dynamic",
    )

    def location_ids(self):
        """All location IDs this employee is assigned to (M2M + legacy location_id)."""
        ids = [loc.id for loc in self.locations]
        if not ids and self.location_id:
            ids = [self.location_id]
        return ids

    def avatar(self, size=128):
        email = self.user_account.email.lower().encode('utf-8') if self.user_account else b"none"
        digest = hashlib.md5(email).hexdigest()
        return f'https://www.gravatar.com/avatar/{digest}?d=identicon&s={size}'

    def get_roadmap_progress(self):
        if not self.active_roadmap:
            return None

        steps = sorted(self.active_roadmap.steps, key=lambda x: x.order_index)
        mastered_count = 0
        target_step = None

        # Only count sessions completed on/after the roadmap was assigned so pre-existing data
        # doesn't automatically skip a trainee past steps they haven't done on this roadmap.
        assigned_date = self.roadmap_assigned_at.date() if self.roadmap_assigned_at else None

        for step in steps:
            sessions = [
                s for s in self.trainee_sessions
                if s.position_id == step.position_id
                and s.completed_at
                and (assigned_date is None or s.session_date >= assigned_date)
            ]

            avg_rating = 0
            if sessions:
                all_ratings = [r.rating_value for s in sessions for r in s.ratings]
                if all_ratings:
                    avg_rating = sum(all_ratings) / len(all_ratings)

            # Mastery Check using dynamic criteria
            req_sessions = step.required_sessions if step.required_sessions else 3
            req_rating = step.min_avg_rating if step.min_avg_rating else 4.0

            is_mastered = len(sessions) >= req_sessions and avg_rating >= req_rating

            if is_mastered:
                mastered_count += 1
            elif not target_step:
                target_step = step

        return {
            'target_position': target_step.position if target_step else None,
            'percent_complete': (mastered_count / len(steps)) * 100 if steps else 0,
            'is_finished': mastered_count == len(steps),
            'mastered_count': mastered_count,
            'total_steps': len(steps)
        }


class Position(db.Model):
    __tablename__ = "positions"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    descriptors = db.relationship("PositionDescriptor", backref="position", cascade="all, delete-orphan")
    location = db.relationship("Location", backref="positions")


class PositionDescriptor(db.Model):
    __tablename__ = "position_descriptors"
    id = db.Column(db.Integer, primary_key=True)
    position_id = db.Column(db.Integer, db.ForeignKey("positions.id"), nullable=False)
    text = db.Column(db.String(255), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class Daypart(db.Model):
    __tablename__ = "dayparts"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    start_time = db.Column(db.Time, nullable=True)
    end_time = db.Column(db.Time, nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class Schedule(db.Model):
    __tablename__ = "schedules"
    id = db.Column(db.Integer, primary_key=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default='draft')
    hide_assignments = db.Column(db.Boolean, default=False)
    published_at = db.Column(db.DateTime, nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    sessions = db.relationship("TrainingSession", backref="schedule", cascade="all, delete-orphan")


class TrainingSession(db.Model):
    __tablename__ = "training_sessions"
    __table_args__ = (
        Index("ix_training_sessions_schedule_date", "schedule_id", "session_date"),
        Index("ix_training_sessions_trainee_completed", "trainee_employee_id", "completed_at"),
    )
    id = db.Column(db.Integer, primary_key=True)
    schedule_id = db.Column(db.Integer, db.ForeignKey("schedules.id"), nullable=False)
    session_date = db.Column(db.Date, nullable=False)
    daypart_id = db.Column(db.Integer, db.ForeignKey("dayparts.id"), nullable=True)
    custom_start_time = db.Column(db.Time, nullable=True)
    custom_end_time = db.Column(db.Time, nullable=True)
    position_id = db.Column(db.Integer, db.ForeignKey("positions.id"), nullable=False)
    trainer_employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)
    trainee_employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    guest_trainer_name = db.Column(db.String(100), nullable=True)
    overall_notes = db.Column(db.Text, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    completed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    rating_scale_used = db.Column(db.Integer, nullable=True)  # scale max when rated (5, 10) for display
    # Digital sign-off
    acknowledged_at = db.Column(db.DateTime, nullable=True)
    signature_data = db.Column(db.Text, nullable=True)  # base64-encoded PNG
    # Needs-attention flag
    flagged = db.Column(db.Boolean, default=False)
    flag_reason = db.Column(db.String(50), nullable=True)
    flag_notes = db.Column(db.Text, nullable=True)
    flag_cleared_at = db.Column(db.DateTime, nullable=True)
    flag_cleared_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    last_nudged_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    daypart = db.relationship("Daypart", foreign_keys=[daypart_id])
    position = db.relationship("Position", foreign_keys=[position_id])
    trainer = db.relationship("Employee", foreign_keys=[trainer_employee_id], backref="trainer_sessions")
    trainee = db.relationship("Employee", foreign_keys=[trainee_employee_id], backref="trainee_sessions")
    completed_by = db.relationship("User", foreign_keys=[completed_by_user_id])
    flag_cleared_by = db.relationship("User", foreign_keys=[flag_cleared_by_user_id])
    ratings = db.relationship("SessionRating", backref="session", cascade="all, delete-orphan")

    @property
    def is_locked(self):
        if self.completed_at: return False
        if not self.daypart_id and not self.custom_start_time: return False
        if not self.schedule or not self.schedule.hide_assignments: return False
        return self.is_future

    @property
    def is_future(self):
        if self.completed_at: return False
        start_t = self.custom_start_time or (self.daypart.start_time if self.daypart else time(8, 0))
        tz = pytz.timezone('US/Eastern')
        now_in_est = datetime.now(tz)
        session_dt = datetime.combine(self.session_date, start_t)
        session_dt_aware = tz.localize(session_dt)
        return now_in_est < session_dt_aware


class SessionRating(db.Model):
    __tablename__ = "session_ratings"
    id = db.Column(db.Integer, primary_key=True)
    training_session_id = db.Column(db.Integer, db.ForeignKey("training_sessions.id"), nullable=False)
    descriptor_id = db.Column(db.Integer, db.ForeignKey("position_descriptors.id"), nullable=False)
    rating_value = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    descriptor = db.relationship("PositionDescriptor", foreign_keys=[descriptor_id])
    __table_args__ = (db.UniqueConstraint("training_session_id", "descriptor_id", name="uq_session_descriptor"),)


class EmployeeNote(db.Model):
    __tablename__ = "employee_notes"
    id = db.Column(db.Integer, primary_key=True)
    trainee_employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    author_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    category = db.Column(db.String(40), nullable=True)
    note_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    trainee = db.relationship("Employee", foreign_keys=[trainee_employee_id])
    author = db.relationship("User", foreign_keys=[author_user_id])


class Notification(db.Model):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_read", "user_id", "read_at"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(140), nullable=False)
    body = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(30), default='general', nullable=False)
    link_url = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    read_at = db.Column(db.DateTime, nullable=True)
    user = db.relationship("User", foreign_keys=[user_id])


class Channel(db.Model):
    """Slack-like channel: either a group channel (name) or a DM (name null, exactly 2 participants)."""
    __tablename__ = "channels"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=True)  # null for DM
    channel_type = db.Column(db.String(20), nullable=False, default="channel")  # 'channel' | 'dm'
    description = db.Column(db.String(255), nullable=True)
    is_private = db.Column(db.Boolean, default=False, nullable=False)
    is_read_only = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)
    created_by = db.relationship("Employee", foreign_keys=[created_by_id])
    participants = db.relationship(
        "ChannelParticipant",
        backref="channel",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    messages = db.relationship(
        "Message",
        backref="channel_obj",
        lazy="dynamic",
        foreign_keys="Message.channel_id",
    )


class ChannelParticipant(db.Model):
    __tablename__ = "channel_participants"
    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(db.Integer, db.ForeignKey("channels.id"), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    joined_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    last_read_at = db.Column(db.DateTime, nullable=True)  # for unread count
    employee = db.relationship("Employee", backref=db.backref("channel_participations", lazy="dynamic"))
    __table_args__ = (db.UniqueConstraint("channel_id", "employee_id", name="uq_channel_participant"),)


class Message(db.Model):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_channel_timestamp", "channel_id", "timestamp"),)
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)  # null when channel_id set
    channel_id = db.Column(db.Integer, db.ForeignKey("channels.id"), nullable=True)  # set for channel/DM channel messages
    body = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, index=True, default=lambda: datetime.now(timezone.utc), nullable=False)
    read_at = db.Column(db.DateTime, nullable=True)  # used for legacy 1:1 only
    reply_to_id = db.Column(db.Integer, db.ForeignKey("messages.id"), nullable=True)
    sender = db.relationship("Employee", foreign_keys=[sender_id], backref=db.backref("messages_sent", lazy="dynamic"))
    recipient = db.relationship("Employee", foreign_keys=[recipient_id],
                                backref=db.backref("messages_received", lazy="dynamic"))
    reply_to = db.relationship("Message", remote_side=[id], foreign_keys=[reply_to_id])


class SystemSettings(db.Model):
    __tablename__ = "system_settings"
    id = db.Column(db.Integer, primary_key=True)
    dm_enabled = db.Column(db.Boolean, default=True)
    allow_trainee_to_trainee_dm = db.Column(db.Boolean, default=True)
    share_trainee_data_with_trainees = db.Column(db.Boolean, default=True)  # When False, trainees cannot see their own training data
    default_hide_assignments = db.Column(db.Boolean, default=False)
    require_completion_notes = db.Column(db.Boolean, default=False)
    require_digital_signoff = db.Column(db.Boolean, default=False)
    default_rating_scale = db.Column(db.Integer, default=5)
    # Setup wizard
    setup_completed = db.Column(db.Boolean, default=False, nullable=False)
    setup_step = db.Column(db.Integer, default=0, nullable=False)
    business_type = db.Column(db.String(80), nullable=True)


class UserSettings(db.Model):
    __tablename__ = "user_settings"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    notify_email = db.Column(db.Boolean, default=True)
    notify_in_app = db.Column(db.Boolean, default=True)
    theme_pref = db.Column(db.String(10), default='system')
    show_online_status = db.Column(db.Boolean, default=True)
    allow_read_receipts = db.Column(db.Boolean, default=True)
    user = db.relationship("User", backref=db.backref("settings", uselist=False))


class MessageReaction(db.Model):
    __tablename__ = 'message_reactions'
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('messages.id'), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    emoji = db.Column(db.String(10), nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    message = db.relationship('Message', backref=db.backref('reactions', lazy='dynamic'))
    employee = db.relationship('Employee')


class TrainingRoadmap(db.Model):
    __tablename__ = "training_roadmaps"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    steps = db.relationship("RoadmapStep", backref="roadmap", cascade="all, delete-orphan",
                            order_by="RoadmapStep.order_index")


class RoadmapStep(db.Model):
    __tablename__ = "roadmap_steps"
    id = db.Column(db.Integer, primary_key=True)
    roadmap_id = db.Column(db.Integer, db.ForeignKey("training_roadmaps.id"), nullable=False)
    position_id = db.Column(db.Integer, db.ForeignKey("positions.id"), nullable=False)
    order_index = db.Column(db.Integer, nullable=False, default=0)
    required_sessions = db.Column(db.Integer, default=3, nullable=False)
    min_avg_rating = db.Column(db.Float, default=4.0, nullable=False)
    position = db.relationship("Position")


class GuestTrainerToken(db.Model):
    __tablename__ = "guest_trainer_tokens"
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(36), unique=True, nullable=False, index=True,
                      default=lambda: str(uuid.uuid4()))
    trainee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    position_id = db.Column(db.Integer, db.ForeignKey("positions.id"), nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    trainee = db.relationship("Employee", foreign_keys=[trainee_id])
    position = db.relationship("Position", foreign_keys=[position_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @property
    def is_valid(self):
        if self.used_at:
            return False
        return datetime.now(timezone.utc) < self.expires_at.replace(tzinfo=timezone.utc)