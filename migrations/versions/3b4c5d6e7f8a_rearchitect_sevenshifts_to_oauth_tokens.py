"""rearchitect sevenshifts to oauth tokens

Revision ID: 3b4c5d6e7f8a
Revises: 2a3b4c5d6e7f
Create Date: 2026-02-27 15:18:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '3b4c5d6e7f8a'
down_revision = '2a3b4c5d6e7f'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        # Drop old per-org credential columns
        batch_op.drop_column('sevenshifts_client_id')
        batch_op.drop_column('sevenshifts_client_secret')
        # Add OAuth token columns (company_id stays)
        batch_op.add_column(sa.Column('sevenshifts_access_token', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('sevenshifts_refresh_token', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('sevenshifts_token_expires_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_column('sevenshifts_token_expires_at')
        batch_op.drop_column('sevenshifts_refresh_token')
        batch_op.drop_column('sevenshifts_access_token')
        batch_op.add_column(sa.Column('sevenshifts_client_id', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('sevenshifts_client_secret', sa.String(length=500), nullable=True))
