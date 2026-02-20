"""Add organizations and organization_id for multi-tenancy

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-02-20

"""
from alembic import op
import sqlalchemy as sa


revision = 'c9d0e1f2a3b4'
down_revision = 'b8c9d0e1f2a3'
branch_labels = None
depends_on = None


def _table_exists(conn, name):
    return name in sa.inspect(conn).get_table_names()


def upgrade():
    conn = op.get_bind()

    # 1. Create organizations table
    op.create_table(
        'organizations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('subdomain', sa.String(80), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_organizations_subdomain', 'organizations', ['subdomain'], unique=True)

    # 2. Add organization_id and is_superuser to users (nullable first)
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('organization_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('is_superuser', sa.Boolean(), nullable=False, server_default='0'))
        batch_op.create_foreign_key('fk_users_organization_id', 'organizations', ['organization_id'], ['id'])

    # 3. Add organization_id to tenant-scoped tables (nullable for backfill)
    for table, fk_name in [
        ('locations', 'fk_locations_organization_id'),
        ('employees', 'fk_employees_organization_id'),
        ('positions', 'fk_positions_organization_id'),
        ('dayparts', 'fk_dayparts_organization_id'),
        ('schedules', 'fk_schedules_organization_id'),
        ('system_settings', 'fk_system_settings_organization_id'),
        ('training_roadmaps', 'fk_training_roadmaps_organization_id'),
        ('channels', 'fk_channels_organization_id'),
        ('training_sessions', 'fk_training_sessions_organization_id'),
    ]:
        if _table_exists(conn, table):
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.add_column(sa.Column('organization_id', sa.Integer(), nullable=True))
                batch_op.create_foreign_key(fk_name, 'organizations', ['organization_id'], ['id'])

    # 4. Backfill: insert default org and set all organization_id
    op.execute(sa.text(
        "INSERT INTO organizations (id, name, subdomain, status, created_at) VALUES (1, 'Default', 'app', 'active', CURRENT_TIMESTAMP)")
    )
    for table in ['users', 'locations', 'employees', 'positions', 'dayparts', 'schedules', 'system_settings', 'training_roadmaps', 'channels']:
        if _table_exists(conn, table):
            op.execute(sa.text(f"UPDATE {table} SET organization_id = 1 WHERE organization_id IS NULL"))
    if _table_exists(conn, 'training_sessions'):
        op.execute(sa.text("UPDATE training_sessions SET organization_id = (SELECT organization_id FROM schedules WHERE schedules.id = training_sessions.schedule_id)"))

    # 5. Make organization_id NOT NULL on tenant tables (users stays nullable for superuser)
    for table in ['locations', 'employees', 'positions', 'dayparts', 'schedules', 'system_settings', 'training_roadmaps', 'channels', 'training_sessions']:
        if _table_exists(conn, table):
            op.alter_column(table, 'organization_id', existing_type=sa.Integer(), nullable=False)

    # 6. Unique constraint on system_settings.organization_id (one row per org)
    with op.batch_alter_table('system_settings', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_system_settings_organization_id', ['organization_id'])

    # 7. Replace users email unique with (organization_id, email) unique
    # Drop existing unique on email; then add (organization_id, email) unique
    if _table_exists(conn, 'users'):
        insp = sa.inspect(conn)
        for uc in insp.get_unique_constraints('users'):
            cols = uc.get('column_names') or []
            if cols == ['email']:
                name = uc.get('name')
                if name:
                    with op.batch_alter_table('users', schema=None) as batch_op:
                        batch_op.drop_constraint(name, type_='unique')
                break
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.create_unique_constraint('uq_user_org_email', ['organization_id', 'email'])

    # 8. Per-org unique on daypart/position names
    if _table_exists(conn, 'dayparts'):
        with op.batch_alter_table('dayparts', schema=None) as batch_op:
            batch_op.create_unique_constraint('uq_daypart_org_name', ['organization_id', 'name'])
    if _table_exists(conn, 'positions'):
        with op.batch_alter_table('positions', schema=None) as batch_op:
            batch_op.create_unique_constraint('uq_position_org_name', ['organization_id', 'name'])


def downgrade():
    conn = op.get_bind()

    # Remove unique constraints
    if _table_exists(conn, 'positions'):
        op.drop_constraint('uq_position_org_name', 'positions', type_='unique')
    if _table_exists(conn, 'dayparts'):
        op.drop_constraint('uq_daypart_org_name', 'dayparts', type_='unique')
    op.drop_constraint('uq_user_org_email', 'users', type_='unique')
    if _table_exists(conn, 'system_settings'):
        op.drop_constraint('uq_system_settings_organization_id', 'system_settings', type_='unique')

    # Restore email unique on users
    op.create_unique_constraint('users_email_key', 'users', ['email'])

    # Drop organization_id from all tables
    for table in ['channels', 'training_roadmaps', 'training_sessions', 'system_settings', 'schedules', 'dayparts', 'positions', 'employees', 'locations']:
        if _table_exists(conn, table):
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_column('organization_id')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('organization_id')
        batch_op.drop_column('is_superuser')

    op.drop_table('organizations')
