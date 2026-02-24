"""Push tokens and notify_push

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-02-20

"""
from alembic import op
import sqlalchemy as sa


revision = 'd1e2f3a4b5c6'
down_revision = 'c0d1e2f3a4b5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'push_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token', sa.String(500), nullable=False),
        sa.Column('platform', sa.String(20), nullable=False, server_default='android'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'token', name='uq_push_token_user_token'),
    )
    op.create_index('ix_push_tokens_token', 'push_tokens', ['token'], unique=False)
    op.create_index('ix_push_tokens_user_id', 'push_tokens', ['user_id'], unique=False)

    op.add_column('user_settings', sa.Column('notify_push', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_index('ix_push_tokens_user_id', 'push_tokens')
    op.drop_index('ix_push_tokens_token', 'push_tokens')
    op.drop_table('push_tokens')
    op.drop_column('user_settings', 'notify_push')
