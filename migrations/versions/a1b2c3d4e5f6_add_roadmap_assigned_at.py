"""Add roadmap_assigned_at to employees

Revision ID: a1b2c3d4e5f6
Revises: 394550b40508
Create Date: 2026-02-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = '394550b40508'
branch_labels = None
depends_on = None


def _column_exists(table_name, column_name):
    conn = op.get_bind()
    insp = sa.inspect(conn)
    return any(c['name'] == column_name for c in insp.get_columns(table_name))


def upgrade():
    if not _column_exists('employees', 'roadmap_assigned_at'):
        with op.batch_alter_table('employees', schema=None) as batch_op:
            batch_op.add_column(sa.Column('roadmap_assigned_at', sa.DateTime(), nullable=True))


def downgrade():
    if _column_exists('employees', 'roadmap_assigned_at'):
        with op.batch_alter_table('employees', schema=None) as batch_op:
            batch_op.drop_column('roadmap_assigned_at')
