"""add position checklist tables

Revision ID: b1c2d3e4f5a6
Revises: a93f55734efb
Create Date: 2026-03-07

"""
from alembic import op
import sqlalchemy as sa


revision = 'b1c2d3e4f5a6'
down_revision = 'a93f55734efb'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'position_checklist_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('position_id', sa.Integer(), nullable=False),
        sa.Column('text', sa.String(length=255), nullable=False),
        sa.Column('order_index', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['position_id'], ['positions.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_position_checklist_items_position_id', 'position_checklist_items', ['position_id'])

    op.create_table(
        'trainee_checklist_completions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('training_session_id', sa.Integer(), nullable=False),
        sa.Column('checklist_item_id', sa.Integer(), nullable=False),
        sa.Column('completed', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('completed_by_user_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['checklist_item_id'], ['position_checklist_items.id'], ),
        sa.ForeignKeyConstraint(['completed_by_user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['training_session_id'], ['training_sessions.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('training_session_id', 'checklist_item_id', name='uq_session_checklist_item'),
    )
    op.create_index('ix_trainee_checklist_completions_session', 'trainee_checklist_completions', ['training_session_id'])


def downgrade():
    op.drop_table('trainee_checklist_completions')
    op.drop_table('position_checklist_items')
