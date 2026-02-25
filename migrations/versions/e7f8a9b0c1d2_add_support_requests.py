"""Add support_requests table for support page form submissions

Revision ID: e7f8a9b0c1d2
Revises: c0d1e2f3a4b5
Create Date: 2026-02-24

"""
from alembic import op
import sqlalchemy as sa


revision = 'e7f8a9b0c1d2'
down_revision = 'c0d1e2f3a4b5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'support_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(120), nullable=True),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('subject', sa.String(200), nullable=False, server_default='Support request'),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_support_requests_email', 'support_requests', ['email'], unique=False)
    op.create_index('ix_support_requests_created_at', 'support_requests', ['created_at'], unique=False)


def downgrade():
    op.drop_index('ix_support_requests_created_at', table_name='support_requests')
    op.drop_index('ix_support_requests_email', table_name='support_requests')
    op.drop_table('support_requests')
