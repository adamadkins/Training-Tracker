"""Add performance indexes

Revision ID: 394550b40508
Revises:
Create Date: 2026-02-17 15:25:50.895984

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '394550b40508'
down_revision = None
branch_labels = None
depends_on = None


def _table_exists(table_name):
    """Check whether a table exists in the current database."""
    conn = op.get_bind()
    insp = sa.inspect(conn)
    return table_name in insp.get_table_names()


def _index_exists(table_name, index_name):
    """Check whether an index already exists on a table."""
    conn = op.get_bind()
    insp = sa.inspect(conn)
    return any(idx['name'] == index_name for idx in insp.get_indexes(table_name))


def upgrade():
    # On a fresh DB the tables may not exist yet (init_db.py creates them after migrations).
    # Skip silently; init_db.py / create_all() will include the indexes from the model definitions.
    if _table_exists('messages'):
        if not _index_exists('messages', 'ix_messages_channel_timestamp'):
            with op.batch_alter_table('messages', schema=None) as batch_op:
                batch_op.create_index('ix_messages_channel_timestamp', ['channel_id', 'timestamp'], unique=False)

    if _table_exists('notifications'):
        if not _index_exists('notifications', 'ix_notifications_user_read'):
            with op.batch_alter_table('notifications', schema=None) as batch_op:
                batch_op.create_index('ix_notifications_user_read', ['user_id', 'read_at'], unique=False)

    if _table_exists('training_sessions'):
        if not _index_exists('training_sessions', 'ix_training_sessions_schedule_date'):
            with op.batch_alter_table('training_sessions', schema=None) as batch_op:
                batch_op.create_index('ix_training_sessions_schedule_date', ['schedule_id', 'session_date'], unique=False)
        if not _index_exists('training_sessions', 'ix_training_sessions_trainee_completed'):
            with op.batch_alter_table('training_sessions', schema=None) as batch_op:
                batch_op.create_index('ix_training_sessions_trainee_completed', ['trainee_employee_id', 'completed_at'], unique=False)


def downgrade():
    if _table_exists('training_sessions'):
        with op.batch_alter_table('training_sessions', schema=None) as batch_op:
            if _index_exists('training_sessions', 'ix_training_sessions_trainee_completed'):
                batch_op.drop_index('ix_training_sessions_trainee_completed')
            if _index_exists('training_sessions', 'ix_training_sessions_schedule_date'):
                batch_op.drop_index('ix_training_sessions_schedule_date')

    if _table_exists('notifications'):
        with op.batch_alter_table('notifications', schema=None) as batch_op:
            if _index_exists('notifications', 'ix_notifications_user_read'):
                batch_op.drop_index('ix_notifications_user_read')

    if _table_exists('messages'):
        with op.batch_alter_table('messages', schema=None) as batch_op:
            if _index_exists('messages', 'ix_messages_channel_timestamp'):
                batch_op.drop_index('ix_messages_channel_timestamp')
