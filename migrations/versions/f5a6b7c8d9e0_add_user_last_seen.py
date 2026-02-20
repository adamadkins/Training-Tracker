"""add user last_seen for online status

Revision ID: f5a6b7c8d9e0
Revises: e4edb5fb55ec
Create Date: 2026-02-19 23:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f5a6b7c8d9e0'
down_revision = 'e4edb5fb55ec'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_seen', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('last_seen')
