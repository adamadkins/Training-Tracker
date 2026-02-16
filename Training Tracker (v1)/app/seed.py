import sys
import os
import glob
import random
from datetime import datetime, timedelta, time, date
from dotenv import load_dotenv
from faker import Faker

# Load environment variables
load_dotenv()

# Ensure the script can find the 'app' package
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app import create_app, db
from app.models import (
    User, Employee, SystemSettings, Position, Daypart,
    Schedule, TrainingSession, PositionDescriptor,
    SessionRating, Message, Notification
)

fake = Faker()


def get_monday(d):
    """Return the Monday of the current week."""
    return d - timedelta(days=d.weekday())


def seed_data():
    app = create_app()
    with app.app_context():
        print("=" * 60)
        print("SEEDING MODE: MASSIVE DATA INJECTION")
        print("=" * 60)

        # 1. Diagnostics
        db_uri = app.config.get('SQLALCHEMY_DATABASE_URI')
        print(f"Target DB: {db_uri}")

        # 2. Reset Database
        print("⚠️  Dropping all tables and recreating... (Fresh Start)")
        db.drop_all()
        db.create_all()

        # --- 1. SYSTEM SETTINGS ---
        print("⚙️  Initializing Settings...")
        settings = SystemSettings(dm_enabled=True, allow_trainee_to_trainee_dm=True)
        db.session.add(settings)

        # --- 2. STATIC DATA (Positions & Dayparts) ---
        print("📋 Creating Positions & Dayparts...")
        positions = [
            "Front Counter", "Drive-Thru Order", "Drive-Thru Window",
            "Fries", "Grill", "Assembly", "Prep", "Expeditor"
        ]
        db_positions = []
        for p in positions:
            pos = Position(name=p, active=True)
            db.session.add(pos)
            db_positions.append(pos)

        # Add descriptors (rubric items) for positions
        for pos in db_positions:
            for _ in range(3):
                desc = PositionDescriptor(position=pos, text=fake.sentence(nb_words=6), active=True)
                db.session.add(desc)

        dayparts = [
            ("Morning", time(6, 0), time(11, 0)),
            ("Lunch", time(11, 0), time(14, 0)),
            ("Afternoon", time(14, 0), time(17, 0)),
            ("Dinner", time(17, 0), time(21, 0))
        ]
        db_dayparts = []
        for name, start, end in dayparts:
            dp = Daypart(name=name, start_time=start, end_time=end)
            db.session.add(dp)
            db_dayparts.append(dp)

        db.session.commit()

        # --- 3. KEY USERS (The ones you log in with) ---
        print("👤 Creating Key Accounts...")

        # Admin
        admin_profile = Employee(first_name="System", last_name="Admin", role="manager", status="active",
                                 start_date=date(2020, 1, 1))
        db.session.add(admin_profile)
        db.session.flush()
        admin_user = User(email="admin@local", role="manager", employee_id=admin_profile.id)
        admin_user.set_password("admin1234")
        db.session.add(admin_user)

        # Trainer
        trainer_profile = Employee(first_name="Terry", last_name="Trainer", role="trainer", status="active",
                                   start_date=date(2021, 5, 15))
        db.session.add(trainer_profile)
        db.session.flush()
        trainer_user = User(email="trainer@local", role="trainer", employee_id=trainer_profile.id)
        trainer_user.set_password("password123")
        db.session.add(trainer_user)

        # Trainee
        trainee_profile = Employee(first_name="Tim", last_name="Trainee", role="trainee", status="active",
                                   start_date=date(2023, 8, 20))
        db.session.add(trainee_profile)
        db.session.flush()
        trainee_user = User(email="trainee@local", role="trainee", employee_id=trainee_profile.id)
        trainee_user.set_password("password123")
        db.session.add(trainee_user)

        # --- 4. BULK EMPLOYEES ---
        print("👥 Generating Bulk Staff...")
        all_employees = [admin_profile, trainer_profile, trainee_profile]

        roles = ['manager'] * 2 + ['trainer'] * 5 + ['trainee'] * 15

        for role in roles:
            first = fake.first_name()
            last = fake.last_name()
            emp = Employee(
                first_name=first,
                last_name=last,
                role=role,
                status="active",
                start_date=fake.date_between(start_date='-2y', end_date='today')
            )
            db.session.add(emp)
            db.session.flush()

            # Create Login for them
            u = User(email=f"{first.lower()}.{last.lower()}@local", role=role, employee_id=emp.id)
            u.set_password("password123")
            db.session.add(u)
            all_employees.append(emp)

        db.session.commit()

        # Separate into groups for easy access
        trainers = [e for e in all_employees if e.role in ['manager', 'trainer']]
        trainees = [e for e in all_employees if e.role == 'trainee']

        # --- 5. SCHEDULES & SESSIONS ---
        print("📅 Creating Schedules & Sessions...")

        today = date.today()
        last_monday = get_monday(today - timedelta(days=7))
        this_monday = get_monday(today)
        next_monday = get_monday(today + timedelta(days=7))

        schedules_data = [
            (last_monday, "published"),
            (this_monday, "published"),
            (next_monday, "draft")
        ]

        for start_dt, status in schedules_data:
            end_dt = start_dt + timedelta(days=6)
            sched = Schedule(start_date=start_dt, end_date=end_dt, status=status)
            db.session.add(sched)
            db.session.flush()

            # Create random sessions for this week
            for _ in range(random.randint(10, 15)):
                session_day = start_dt + timedelta(days=random.randint(0, 6))
                t_r = random.choice(trainers)
                t_e = random.choice(trainees)
                pos = random.choice(db_positions)

                use_daypart = random.choice([True, True, False])
                dp_id = random.choice(db_dayparts).id if use_daypart else None
                c_start = None
                c_end = None

                if not use_daypart:
                    hour = random.randint(8, 16)
                    c_start = time(hour, 0)
                    c_end = time(hour + 4, 0)

                session = TrainingSession(
                    schedule_id=sched.id,
                    trainer_employee_id=t_r.id,
                    trainee_employee_id=t_e.id,
                    position_id=pos.id,
                    session_date=session_day,
                    daypart_id=dp_id,
                    custom_start_time=c_start,
                    custom_end_time=c_end
                )

                if start_dt == last_monday:
                    session.completed_at = datetime.combine(session_day, time(17, 0))
                    session.overall_notes = fake.paragraph(nb_sentences=2)
                    db.session.add(session)
                    db.session.flush()

                    descriptors = PositionDescriptor.query.filter_by(position_id=pos.id).all()
                    for desc in descriptors:
                        rating = SessionRating(
                            training_session_id=session.id,
                            descriptor_id=desc.id,
                            rating_value=random.randint(2, 5),
                            comment=fake.sentence() if random.choice([True, False]) else None
                        )
                        db.session.add(rating)

                elif start_dt == this_monday:
                    if session_day < today:
                        session.completed_at = datetime.combine(session_day, time(17, 0))
                        session.overall_notes = "Training completed successfully."
                        db.session.add(session)
                        db.session.flush()

                        descriptors = PositionDescriptor.query.filter_by(position_id=pos.id).all()
                        for desc in descriptors:
                            rating = SessionRating(
                                training_session_id=session.id,
                                descriptor_id=desc.id,
                                rating_value=random.randint(3, 5)
                            )
                            db.session.add(rating)
                    else:
                        db.session.add(session)
                else:
                    db.session.add(session)

        # --- 6. MESSAGES ---
        print("💬 Generating Conversations...")
        for _ in range(20):
            sender = random.choice(all_employees)
            recipient = random.choice(all_employees)
            if sender.id == recipient.id: continue

            base_time = datetime.now() - timedelta(days=random.randint(0, 5))

            for i in range(random.randint(2, 6)):
                msg_time = base_time + timedelta(minutes=i * 10)
                msg = Message(
                    sender_id=sender.id,
                    recipient_id=recipient.id,
                    body=fake.sentence(nb_words=10),
                    timestamp=msg_time,
                    read_at=msg_time + timedelta(minutes=5) if i < 2 else None
                )
                db.session.add(msg)
                sender, recipient = recipient, sender

        # --- 7. NOTIFICATIONS ---
        print("🔔 Queuing Notifications...")
        for user in all_employees[:10]:
            if user.user_account:
                # REMOVED 'type' argument entirely
                note = Notification(
                    user_id=user.user_account.id,
                    title="New Schedule Posted",
                    body=f"The schedule for {next_monday.strftime('%b %d')} is now available.",
                    created_at=datetime.now() - timedelta(hours=2)
                )
                db.session.add(note)

        db.session.commit()

        print("\n" + "=" * 60)
        print("✅  SEED COMPLETE")
        print("=" * 60)
        print("1. Manager: admin@local   / admin1234")
        print("2. Trainer: trainer@local / password123")
        print("3. Trainee: trainee@local / password123")
        print(f"4. Plus {len(all_employees) - 3} extra users (pass: password123)")
        print("=" * 60)


if __name__ == "__main__":
    seed_data()