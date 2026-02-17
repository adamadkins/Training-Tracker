"""
Seed the database with a coherent, demo-ready dataset.
Uses realistic names, training progressions, and conversations
so the app behaves like a real (but fake) team in action.
"""
import sys
import os
import random
from datetime import datetime, timedelta, time, date, timezone
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app import create_app, db
from app.models import (
    User, Employee, SystemSettings, Position, Daypart,
    Schedule, TrainingSession, PositionDescriptor,
    SessionRating, Message, Notification,
    Channel, ChannelParticipant,
)


def get_monday(d):
    """Return the Monday of the current week."""
    return d - timedelta(days=d.weekday())


# ─── Realistic descriptor text per position (rubric criteria) ───
POSITION_DESCRIPTORS = {
    "Front Counter": [
        "Greets guests promptly and with a smile",
        "Handles cash and card payments accurately",
        "Suggests add-ons and completes orders correctly",
    ],
    "Drive-Thru Order": [
        "Takes order clearly and repeats back for accuracy",
        "Maintains speed without sacrificing accuracy",
        "Upsells appropriately and closes the order",
    ],
    "Drive-Thru Window": [
        "Handles payment and hands out order efficiently",
        "Keeps window area clean and organized",
        "Verifies order with guest before sending them on",
    ],
    "Fries": [
        "Maintains fry station and baskets properly",
        "Portions fries consistently to standard",
        "Keeps fry station clean and stocked",
    ],
    "Grill": [
        "Follows safety procedures and cook times",
        "Maintains grill temperature and cleanliness",
        "Cooks to spec and restocks as needed",
    ],
    "Assembly": [
        "Builds items to spec and in the correct order",
        "Keeps assembly area organized and stocked",
        "Works in sync with grill and expeditor",
    ],
    "Prep": [
        "Completes prep list on time and to standard",
        "Labels and dates all prepped items correctly",
        "Keeps walk-in and prep area organized",
    ],
    "Expeditor": [
        "Calls out orders clearly and coordinates flow",
        "Quality-checks items before pass-out",
        "Keeps pass-through organized and timely",
    ],
}


def _run_seed(app, drop_first=True):
    """Run seed logic. If drop_first=True, drop and recreate tables then insert. Otherwise only insert (tables must exist)."""
    with app.app_context():
        print("=" * 60)
        print("SEEDING: Demo-ready data (realistic, coherent)")
        print("=" * 60)
        print(f"Target DB: {app.config.get('SQLALCHEMY_DATABASE_URI')}")

        if drop_first:
            print("\n⚠️  Dropping all tables and recreating...")
            db.drop_all()
            db.create_all()
        else:
            print("\n📥 Database empty — inserting demo data...")

        # ─── 1. System settings ───
        print("⚙️  System settings...")
        settings = SystemSettings(
            dm_enabled=True,
            allow_trainee_to_trainee_dm=True,
            setup_completed=True,
            default_rating_scale=5,
        )
        db.session.add(settings)
        db.session.commit()

        # ─── 2. Positions & descriptors ───
        print("📋 Positions & rubric descriptors...")
        position_names = list(POSITION_DESCRIPTORS.keys())
        db_positions = []
        for name in position_names:
            pos = Position(name=name, active=True)
            db.session.add(pos)
            db.session.flush()
            db_positions.append(pos)
            for text in POSITION_DESCRIPTORS[name]:
                desc = PositionDescriptor(position_id=pos.id, text=text, active=True)
                db.session.add(desc)
        db.session.commit()

        # ─── 3. Dayparts ───
        dayparts_data = [
            ("Morning", time(6, 0), time(11, 0)),
            ("Lunch", time(11, 0), time(14, 0)),
            ("Afternoon", time(14, 0), time(17, 0)),
            ("Dinner", time(17, 0), time(21, 0)),
        ]
        db_dayparts = []
        for name, start, end in dayparts_data:
            dp = Daypart(name=name, start_time=start, end_time=end)
            db.session.add(dp)
            db_dayparts.append(dp)
        db.session.commit()

        # ─── 4. Curated team (fixed names for a readable demo) ───
        print("👤 Building team...")
        team_data = [
            # (first, last, role) — first three use admin@local, trainer@local, trainee@local
            ("System", "Admin", "manager"),
            ("Terry", "Trainer", "trainer"),
            ("Tim", "Trainee", "trainee"),
            # Additional staff (firstname.lastname@demo.local)
            ("Morgan", "Lee", "manager"),
            ("Jordan", "Smith", "trainer"),
            ("Alex", "Rivera", "trainer"),
            ("Casey", "Jones", "trainer"),
            ("Riley", "Clark", "trainee"),
            ("Jamie", "Wright", "trainee"),
            ("Quinn", "Martinez", "trainee"),
            ("Avery", "Taylor", "trainee"),
            ("Parker", "Brown", "trainee"),
            ("Blake", "Davis", "trainee"),
        ]
        all_employees = []
        for first, last, role in team_data:
            start = date(2022, 1, 1) if role in ("manager", "trainer") else date(2023, 6, 1) + timedelta(days=random.randint(0, 200))
            emp = Employee(
                first_name=first,
                last_name=last,
                role=role,
                status="active",
                start_date=start,
            )
            db.session.add(emp)
            db.session.flush()
            login_emails = ["admin@local", "trainer@local", "trainee@local"]
            idx = len(all_employees)
            email = login_emails[idx] if idx < 3 else f"{first.lower()}.{last.lower()}@demo.local"
            user = User(email=email, role=role, employee_id=emp.id)
            user.set_password("admin1234" if email == "admin@local" else "password123")
            db.session.add(user)
            all_employees.append(emp)
        db.session.commit()

        managers = [e for e in all_employees if e.role == "manager"]
        trainers = [e for e in all_employees if e.role == "trainer"]
        trainees = [e for e in all_employees if e.role == "trainee"]

        # ─── 5. Schedules & training sessions (coherent story) ───
        print("📅 Schedules & training sessions...")
        today = date.today()
        last_monday = get_monday(today - timedelta(days=7))
        this_monday = get_monday(today)
        next_monday = get_monday(today + timedelta(days=7))

        for start_dt, status in [(last_monday, "published"), (this_monday, "published"), (next_monday, "draft")]:
            end_dt = start_dt + timedelta(days=6)
            sched = Schedule(start_date=start_dt, end_date=end_dt, status=status)
            db.session.add(sched)
            db.session.flush()

            # Assign sessions so trainees have a logical progression (e.g. same position a few times, then next)
            positions_ordered = db_positions[:5]  # Front Counter -> Drive-Thru Order -> Window -> Fries -> Grill
            for ti, trainee in enumerate(trainees):
                for _ in range(2):
                    session_day = start_dt + timedelta(days=random.randint(0, 6))
                    pos = positions_ordered[ti % len(positions_ordered)]
                    tr = random.choice(trainers)
                    use_dp = random.choice([True, True, False])
                    dp_id = random.choice(db_dayparts).id if use_dp else None
                    c_start = time(9, 0) if not use_dp else None
                    c_end = time(13, 0) if not use_dp else None

                    session = TrainingSession(
                        schedule_id=sched.id,
                        trainer_employee_id=tr.id,
                        trainee_employee_id=trainee.id,
                        position_id=pos.id,
                        session_date=session_day,
                        daypart_id=dp_id,
                        custom_start_time=c_start,
                        custom_end_time=c_end,
                    )
                    if start_dt == last_monday or (start_dt == this_monday and session_day < today):
                        session.completed_at = datetime.combine(session_day, time(16, 30))
                        session.overall_notes = random.choice([
                            "Solid shift. Ready to move on next week.",
                            "Did great. No issues.",
                            "Good progress on speed. Will repeat once more then advance.",
                        ])
                        db.session.add(session)
                        db.session.flush()
                        for desc in PositionDescriptor.query.filter_by(position_id=pos.id).all():
                            r = SessionRating(
                                training_session_id=session.id,
                                descriptor_id=desc.id,
                                rating_value=random.randint(3, 5),
                                comment=random.choice([None, None, "Good.", "Nailed it."]),
                            )
                            db.session.add(r)
                    else:
                        db.session.add(session)
        db.session.commit()

        # ─── 6. Channels & messages (realistic conversations) ───
        print("💬 Channels & messages...")
        now = datetime.now(timezone.utc)

        # General channel (everyone)
        ch_general = Channel(
            name="general",
            channel_type="channel",
            description="Store-wide announcements and chat.",
            is_private=False,
            is_read_only=False,
            created_by_id=managers[0].id,
        )
        db.session.add(ch_general)
        db.session.flush()
        for emp in all_employees:
            db.session.add(ChannelParticipant(channel_id=ch_general.id, employee_id=emp.id))
        general_messages = [
            (managers[0], "Hey everyone — the new schedule is posted. Please check your shifts and let a manager know if you need any swaps.", now - timedelta(days=2, hours=10)),
            (trainers[0], "Thanks! I'll take a look.", now - timedelta(days=2, hours=9, minutes=30)),
            (managers[1], "Reminder: shift swap requests need to be in by Friday noon.", now - timedelta(days=1, hours=8)),
            (trainers[1], "Who's covering the close on Saturday? I might need to swap.", now - timedelta(hours=5)),
            (managers[0], "I'll post the closing roster by tomorrow.", now - timedelta(hours=4, minutes=45)),
        ]
        general_messages.sort(key=lambda x: x[2])
        for sender, body, ts in general_messages:
            msg = Message(sender_id=sender.id, recipient_id=None, channel_id=ch_general.id, body=body, timestamp=ts)
            db.session.add(msg)
        db.session.flush()
        for cp in ChannelParticipant.query.filter_by(channel_id=ch_general.id).all():
            cp.last_read_at = now - timedelta(hours=1)
        db.session.commit()

        # Managers-only (private)
        ch_managers = Channel(
            name="managers",
            channel_type="channel",
            description="Manager-only discussions.",
            is_private=True,
            is_read_only=False,
            created_by_id=managers[0].id,
        )
        db.session.add(ch_managers)
        db.session.flush()
        for emp in managers:
            db.session.add(ChannelParticipant(channel_id=ch_managers.id, employee_id=emp.id))
        msg_m = Message(
            sender_id=managers[0].id, recipient_id=None, channel_id=ch_managers.id,
            body="Quick heads up — we're doing inventory Tuesday night. Can one of you stay late?",
            timestamp=now - timedelta(days=1, hours=14),
        )
        db.session.add(msg_m)

        # Schedule updates (read-only for trainees)
        ch_schedule = Channel(
            name="schedule-updates",
            channel_type="channel",
            description="Schedule and roster updates. Only managers post here.",
            is_private=False,
            is_read_only=True,
            created_by_id=managers[0].id,
        )
        db.session.add(ch_schedule)
        db.session.flush()
        for emp in all_employees:
            db.session.add(ChannelParticipant(channel_id=ch_schedule.id, employee_id=emp.id))
        msg_s = Message(
            sender_id=managers[0].id, recipient_id=None, channel_id=ch_schedule.id,
            body="Week of " + this_monday.strftime("%B %d") + " schedule is live. Check the app for your assignments.",
            timestamp=now - timedelta(days=3, hours=9),
        )
        db.session.add(msg_s)

        # DM channel: Terry (trainer) <-> Tim (trainee)
        terry = next(e for e in all_employees if e.first_name == "Terry" and e.role == "trainer")
        tim = next(e for e in all_employees if e.first_name == "Tim" and e.role == "trainee")
        ch_dm = Channel(name=None, channel_type="dm", created_by_id=terry.id)
        db.session.add(ch_dm)
        db.session.flush()
        db.session.add(ChannelParticipant(channel_id=ch_dm.id, employee_id=terry.id))
        db.session.add(ChannelParticipant(channel_id=ch_dm.id, employee_id=tim.id))
        dm_messages = [
            (terry, "You're on Front Counter with me Tuesday lunch. We'll work on speed.", now - timedelta(days=2, hours=12)),
            (tim, "Sounds good, see you then.", now - timedelta(days=2, hours=11, minutes=30)),
            (terry, "Great job today — you're ready for drive-thru order next week.", now - timedelta(days=1, hours=15)),
            (tim, "Thanks! I'll check the schedule.", now - timedelta(days=1, hours=16)),
        ]
        for sender, body, ts in dm_messages:
            db.session.add(Message(sender_id=sender.id, recipient_id=None, channel_id=ch_dm.id, body=body, timestamp=ts))

        db.session.commit()

        # ─── 7. Notifications (realistic, with links where useful) ───
        print("🔔 Notifications...")
        schedule_path = "/manager/schedules"
        for emp in all_employees[:8]:
            u = getattr(emp, "user_account", None)
            if not u:
                continue
            note = Notification(
                user_id=u.id,
                title="New schedule posted",
                body=f"The schedule for the week of {next_monday.strftime('%b %d')} is now available. Check your assignments.",
                category="schedule",
                link_url=schedule_path,
                created_at=now - timedelta(hours=2),
            )
            db.session.add(note)
        db.session.commit()

        print("\n" + "=" * 60)
        print("✅  SEED COMPLETE — Demo-ready data")
        print("=" * 60)
        print("Log in with (password for all: password123):")
        print("  • Manager:  admin@local  / admin1234  (System Admin)")
        print("  • Trainer:  trainer@local / password123 (Terry Trainer)")
        print("  • Trainee:  trainee@local / password123 (Tim Trainee)")
        print("  • Others:  firstname.lastname@demo.local / password123")
        print("=" * 60)


def seed_if_empty(app):
    """If the database has no system settings (empty), run seed without dropping. Call from app startup."""
    from sqlalchemy.exc import OperationalError, IntegrityError
    with app.app_context():
        try:
            if SystemSettings.query.first() is not None:
                return
        except OperationalError:
            # Tables might not exist yet (e.g. fresh Postgres); ensure they do
            db.create_all()
        try:
            _run_seed(app, drop_first=False)
        except IntegrityError:
            # Another worker/process already seeded
            db.session.rollback()
            return


def seed_data():
    """CLI entrypoint: create app and run full seed (drop + recreate + insert)."""
    app = create_app()
    _run_seed(app, drop_first=True)


if __name__ == "__main__":
    seed_data()
