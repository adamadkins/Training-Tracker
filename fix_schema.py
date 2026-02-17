import sqlite3
import os

# --- 1. CONFIGURATION: List all your schema changes here ---
# Format: Just the raw SQL command you want to run.
schema_changes = [
    # Old changes (Roadmap Steps)
    "ALTER TABLE roadmap_steps ADD COLUMN required_sessions INTEGER DEFAULT 3 NOT NULL",
    "ALTER TABLE roadmap_steps ADD COLUMN min_avg_rating FLOAT DEFAULT 4.0 NOT NULL",

    # System Settings
    "ALTER TABLE system_settings ADD COLUMN default_hide_assignments BOOLEAN DEFAULT 0",
    "ALTER TABLE system_settings ADD COLUMN require_completion_notes BOOLEAN DEFAULT 0",
    "ALTER TABLE system_settings ADD COLUMN default_rating_scale INTEGER DEFAULT 5",
    "ALTER TABLE training_sessions ADD COLUMN rating_scale_used INTEGER",

    # Setup wizard + locations
    "CREATE TABLE IF NOT EXISTS locations (id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, name VARCHAR(80) NOT NULL, description VARCHAR(255), created_at DATETIME)",
    "ALTER TABLE employees ADD COLUMN location_id INTEGER REFERENCES locations(id)",
    "ALTER TABLE system_settings ADD COLUMN setup_completed BOOLEAN DEFAULT 0 NOT NULL",
    "ALTER TABLE system_settings ADD COLUMN setup_step INTEGER DEFAULT 0 NOT NULL",
    "ALTER TABLE system_settings ADD COLUMN business_type VARCHAR(80)",
    "ALTER TABLE employees ADD COLUMN graduated_at DATETIME",

    # Message replies
    "ALTER TABLE messages ADD COLUMN reply_to_id INTEGER REFERENCES messages(id)",

    # Manager: allow sharing trainee data with trainees
    "ALTER TABLE system_settings ADD COLUMN share_trainee_data_with_trainees BOOLEAN DEFAULT 1",

    # Slack-like channels (group chats + DM channels)
    "CREATE TABLE IF NOT EXISTS channels (id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, name VARCHAR(80), channel_type VARCHAR(20) NOT NULL DEFAULT 'channel', created_at DATETIME, created_by_id INTEGER REFERENCES employees(id))",
    "CREATE TABLE IF NOT EXISTS channel_participants (id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, channel_id INTEGER NOT NULL REFERENCES channels(id), employee_id INTEGER NOT NULL REFERENCES employees(id), joined_at DATETIME, last_read_at DATETIME, UNIQUE(channel_id, employee_id))",
    "ALTER TABLE messages ADD COLUMN channel_id INTEGER REFERENCES channels(id)",

    # Notification enhancements
    "ALTER TABLE notifications ADD COLUMN category VARCHAR(30) DEFAULT 'general' NOT NULL",
    "ALTER TABLE notifications ADD COLUMN link_url VARCHAR(500)",

    # Digital sign-off
    "ALTER TABLE system_settings ADD COLUMN require_digital_signoff BOOLEAN DEFAULT 0",
    "ALTER TABLE training_sessions ADD COLUMN acknowledged_at DATETIME",
    "ALTER TABLE training_sessions ADD COLUMN signature_data TEXT",

    # Needs-attention flagging
    "ALTER TABLE training_sessions ADD COLUMN flagged BOOLEAN DEFAULT 0",
    "ALTER TABLE training_sessions ADD COLUMN flag_reason VARCHAR(50)",
    "ALTER TABLE training_sessions ADD COLUMN flag_notes TEXT",
    "ALTER TABLE training_sessions ADD COLUMN flag_cleared_at DATETIME",
    "ALTER TABLE training_sessions ADD COLUMN flag_cleared_by_user_id INTEGER REFERENCES users(id)",

    # First-time tutorial
    "ALTER TABLE users ADD COLUMN has_seen_tutorial BOOLEAN DEFAULT 0",

    # Position-location scoping
    "ALTER TABLE positions ADD COLUMN location_id INTEGER REFERENCES locations(id)",

    # Employee multiple locations (cross-training; managers see multiple)
    "CREATE TABLE IF NOT EXISTS employee_locations (employee_id INTEGER NOT NULL REFERENCES employees(id), location_id INTEGER NOT NULL REFERENCES locations(id), PRIMARY KEY (employee_id, location_id))",
    "INSERT OR IGNORE INTO employee_locations (employee_id, location_id) SELECT id, location_id FROM employees WHERE location_id IS NOT NULL",

    # Channel settings: private, read-only, description
    "ALTER TABLE channels ADD COLUMN description VARCHAR(255)",
    "ALTER TABLE channels ADD COLUMN is_private BOOLEAN DEFAULT 0 NOT NULL",
    "ALTER TABLE channels ADD COLUMN is_read_only BOOLEAN DEFAULT 0 NOT NULL",
]

# --- 2. SETUP: Find the database ---
possible_paths = ['instance/training_tracker.db', 'training_tracker.db', 'database.db', 'app.db']
db_path = None

for path in possible_paths:
    if os.path.exists(path):
        db_path = path
        break

if not db_path:
    print("❌ Error: Could not find your database file.")
    exit()

print(f"📍 Found database at: {db_path}")

# --- 3. EXECUTION: Run the changes safely ---
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print(f"🔄 Attempting {len(schema_changes)} schema updates...")

for sql_command in schema_changes:
    try:
        cursor.execute(sql_command)
        print(f"✅ Success: {sql_command}")
    except sqlite3.OperationalError as e:
        # SQLite throws an error if the column already exists.
        # We check for that specific message to know if it's safe to skip.
        if "duplicate column name" in str(e):
            print(f"ℹ️  Skipped (already exists): {sql_command.split('ADD COLUMN')[1].split()[0]}")
        elif "no such table" in str(e):
            print(f"⚠️  Warning: Table not found for command: {sql_command}")
        else:
            print(f"❌ Error running command: {sql_command}\n   Reason: {e}")

conn.commit()
conn.close()
print("🎉 Database patch routine complete!")