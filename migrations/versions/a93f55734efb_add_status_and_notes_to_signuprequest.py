"""add status and notes to SignupRequest

Revision ID: a93f55734efb
Revises: 6018875a99cf
Create Date: 2026-03-05 21:41:32.545798

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a93f55734efb'
down_revision = '6018875a99cf'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('signup_requests', schema=None) as batch_op:
        batch_op.add_column(sa.Column('status', sa.String(length=20), server_default="new", nullable=False))
        batch_op.add_column(sa.Column('notes', sa.Text(), nullable=True))

def downgrade():
    with op.batch_alter_table('signup_requests', schema=None) as batch_op:
        batch_op.drop_column('notes')
        batch_op.drop_column('status')
