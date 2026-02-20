"""add branding primary_color and custom_logo_url

Revision ID: a7b8c9d0e1f2
Revises: f5a6b7c8d9e0
Create Date: 2026-02-19 24:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a7b8c9d0e1f2'
down_revision = 'f5a6b7c8d9e0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('system_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('primary_color', sa.String(20), nullable=False, server_default='indigo'))
        batch_op.add_column(sa.Column('custom_logo_url', sa.String(500), nullable=True))


def downgrade():
    with op.batch_alter_table('system_settings', schema=None) as batch_op:
        batch_op.drop_column('custom_logo_url')
        batch_op.drop_column('primary_color')
