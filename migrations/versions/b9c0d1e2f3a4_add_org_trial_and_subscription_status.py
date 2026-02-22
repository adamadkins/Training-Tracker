"""Add org trial (14-day) and stripe_subscription_status

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-02-20

"""
from alembic import op
import sqlalchemy as sa


revision = 'b9c0d1e2f3a4'
down_revision = 'a8b9c0d1e2f3'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organizations', sa.Column('stripe_subscription_status', sa.String(30), nullable=True))
    op.add_column('organizations', sa.Column('trial_ends_at', sa.DateTime(), nullable=True))
    op.add_column('organizations', sa.Column('trial_plan', sa.String(20), nullable=True))


def downgrade():
    op.drop_column('organizations', 'trial_plan')
    op.drop_column('organizations', 'trial_ends_at')
    op.drop_column('organizations', 'stripe_subscription_status')
