"""add hs_api columns to organizations

Revision ID: 101ec71d4920
Revises: e7f8a9b0c1d2
Create Date: 2026-02-27 14:29:59.273013

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '101ec71d4920'
down_revision = 'e7f8a9b0c1d2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('hs_api_url', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('hs_api_username', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('hs_api_password', sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_column('hs_api_password')
        batch_op.drop_column('hs_api_username')
        batch_op.drop_column('hs_api_url')
