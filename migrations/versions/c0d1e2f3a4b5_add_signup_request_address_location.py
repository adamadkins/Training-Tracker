"""Add signup request address, phone, location_identifier

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-02-20

"""
from alembic import op
import sqlalchemy as sa


revision = 'c0d1e2f3a4b5'
down_revision = 'b9c0d1e2f3a4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('signup_requests', sa.Column('location_identifier', sa.String(120), nullable=True))
    op.add_column('signup_requests', sa.Column('phone', sa.String(40), nullable=True))
    op.add_column('signup_requests', sa.Column('address_line1', sa.String(255), nullable=True))
    op.add_column('signup_requests', sa.Column('city', sa.String(80), nullable=True))
    op.add_column('signup_requests', sa.Column('state', sa.String(80), nullable=True))
    op.add_column('signup_requests', sa.Column('postal_code', sa.String(20), nullable=True))


def downgrade():
    op.drop_column('signup_requests', 'postal_code')
    op.drop_column('signup_requests', 'state')
    op.drop_column('signup_requests', 'city')
    op.drop_column('signup_requests', 'address_line1')
    op.drop_column('signup_requests', 'phone')
    op.drop_column('signup_requests', 'location_identifier')
