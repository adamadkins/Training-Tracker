"""add sevenshifts api columns to organizations

Revision ID: 2a3b4c5d6e7f
Revises: 101ec71d4920
Create Date: 2026-02-27 14:52:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2a3b4c5d6e7f'
down_revision = '101ec71d4920'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sevenshifts_client_id', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('sevenshifts_client_secret', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('sevenshifts_company_id', sa.String(length=100), nullable=True))


def downgrade():
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_column('sevenshifts_company_id')
        batch_op.drop_column('sevenshifts_client_secret')
        batch_op.drop_column('sevenshifts_client_id')
