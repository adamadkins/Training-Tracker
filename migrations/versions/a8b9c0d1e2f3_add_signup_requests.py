"""Add signup_requests table for landing page submissions

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-02-20

"""
from alembic import op
import sqlalchemy as sa


revision = 'a8b9c0d1e2f3'
down_revision = 'f7a8b9c0d1e2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'signup_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(120), nullable=False, server_default=''),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('business', sa.String(200), nullable=False, server_default=''),
        sa.Column('size', sa.String(40), nullable=True),
        sa.Column('plan', sa.String(20), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_signup_requests_email', 'signup_requests', ['email'], unique=False)
    op.create_index('ix_signup_requests_created_at', 'signup_requests', ['created_at'], unique=False)


def downgrade():
    op.drop_index('ix_signup_requests_created_at', table_name='signup_requests')
    op.drop_index('ix_signup_requests_email', table_name='signup_requests')
    op.drop_table('signup_requests')
