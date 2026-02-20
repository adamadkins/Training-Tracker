"""add business_name to system_settings

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-02-20

"""
from alembic import op
import sqlalchemy as sa


revision = 'b8c9d0e1f2a3'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('system_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('business_name', sa.String(120), nullable=True))


def downgrade():
    with op.batch_alter_table('system_settings', schema=None) as batch_op:
        batch_op.drop_column('business_name')
