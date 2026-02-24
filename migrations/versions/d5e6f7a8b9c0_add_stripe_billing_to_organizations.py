"""Add Stripe billing fields to organizations

Revision ID: d5e6f7a8b9c0
Revises: c9d0e1f2a3b4
Create Date: 2026-02-20

"""
from alembic import op
import sqlalchemy as sa


revision = 'd5e6f7a8b9c0'
down_revision = 'c9d0e1f2a3b4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('stripe_customer_id', sa.String(255), nullable=True))
        batch_op.add_column(sa.Column('stripe_subscription_id', sa.String(255), nullable=True))
    op.create_index('ix_organizations_stripe_customer_id', 'organizations', ['stripe_customer_id'], unique=False)
    op.create_index('ix_organizations_stripe_subscription_id', 'organizations', ['stripe_subscription_id'], unique=False)


def downgrade():
    op.drop_index('ix_organizations_stripe_subscription_id', table_name='organizations')
    op.drop_index('ix_organizations_stripe_customer_id', table_name='organizations')
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_column('stripe_subscription_id')
        batch_op.drop_column('stripe_customer_id')
