"""add trial_expiration_email_sent to organizations

Revision ID: c1d2e3f4a5b6
Revises: b1c2d3e4f5a6
Create Date: 2026-03-14

"""
from alembic import op
import sqlalchemy as sa


revision = 'c1d2e3f4a5b6'
down_revision = 'b1c2d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organizations', sa.Column('trial_expiration_email_sent', sa.Boolean(), nullable=True))
    op.execute("UPDATE organizations SET trial_expiration_email_sent = false WHERE trial_expiration_email_sent IS NULL")
    op.alter_column('organizations', 'trial_expiration_email_sent', nullable=False, server_default=sa.text('false'))


def downgrade():
    op.drop_column('organizations', 'trial_expiration_email_sent')
