"""Add organization_id to signup_requests

Revision ID: 6018875a99cf
Revises: 3b4c5d6e7f8a
Create Date: 2026-03-02 20:14:42.499877

"""
from alembic import op
import sqlalchemy as sa


revision = '6018875a99cf'
down_revision = '3b4c5d6e7f8a'
branch_labels = None
depends_on = None


def _column_exists(bind, table, column):
    from sqlalchemy import inspect
    insp = inspect(bind)
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def upgrade():
    bind = op.get_bind()
    if not _column_exists(bind, "signup_requests", "organization_id"):
        op.add_column('signup_requests', sa.Column('organization_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_signup_requests_organization_id'), 'signup_requests', ['organization_id'], unique=False, if_not_exists=True)
    if bind.dialect.name != 'sqlite':
        op.create_foreign_key('fk_signup_requests_organization_id', 'signup_requests', 'organizations', ['organization_id'], ['id'])


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'sqlite':
        op.drop_constraint('fk_signup_requests_organization_id', 'signup_requests', type_='foreignkey')
    op.drop_index(op.f('ix_signup_requests_organization_id'), table_name='signup_requests')
    op.drop_column('signup_requests', 'organization_id')
