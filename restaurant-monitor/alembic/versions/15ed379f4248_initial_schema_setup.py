"""Initial schema setup

Revision ID: 15ed379f4248
Revises: 
Create Date: 2026-07-31 18:38:22.762176

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '15ed379f4248'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create admins table
    op.create_table(
        'admins',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('email', sa.String(length=150), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('otp_code_hash', sa.String(length=255), nullable=True),
        sa.Column('otp_expires_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )
    op.create_index(op.f('ix_admins_email'), 'admins', ['email'], unique=False)

    # Create cameras table
    op.create_table(
        'cameras',
        sa.Column('id', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('rtsp_url', sa.String(length=500), nullable=False),
        sa.Column('status', sa.Enum('ONLINE', 'OFFLINE', name='camerastatus'), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.Column('frame_width', sa.Integer(), nullable=True),
        sa.Column('frame_height', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create zones table
    op.create_table(
        'zones',
        sa.Column('id', sa.String(length=100), nullable=False),
        sa.Column('camera_id', sa.String(length=50), nullable=False),
        sa.Column('parent_zone_id', sa.String(length=100), nullable=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('zone_type', sa.String(length=50), nullable=False),
        sa.Column('polygon_coordinates', sa.JSON(), nullable=False),
        sa.Column('auto_generated', sa.Boolean(), nullable=True),
        sa.Column('excludes_tables', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['camera_id'], ['cameras.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_zone_id'], ['zones.id']),
        sa.PrimaryKeyConstraint('id')
    )

    # Create system_settings table
    op.create_table(
        'system_settings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('worker_idle_limit', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create customer_sessions table
    op.create_table(
        'customer_sessions',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('entity_id', sa.String(length=50), nullable=False),
        sa.Column('table_zone_id', sa.String(length=100), nullable=False),
        sa.Column('camera_id', sa.String(length=50), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False),
        sa.Column('left_at', sa.DateTime(), nullable=True),
        sa.Column('total_stay_sec', sa.Integer(), nullable=True),
        sa.Column('status', sa.Enum('ACTIVE', 'COMPLETED', name='customersessionstatus'), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['camera_id'], ['cameras.id']),
        sa.ForeignKeyConstraint(['table_zone_id'], ['zones.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_customer_sessions_entity_id'), 'customer_sessions', ['entity_id'], unique=False)

    # Create worker_states table
    op.create_table(
        'worker_states',
        sa.Column('entity_id', sa.String(length=50), nullable=False),
        sa.Column('role', sa.Enum('WORKER', 'CUSTOMER', 'UNKNOWN', name='workerrole'), nullable=True),
        sa.Column('camera_id', sa.String(length=50), nullable=False),
        sa.Column('zone_id', sa.String(length=100), nullable=True),
        sa.Column('status', sa.Enum('ACTIVE', 'IDLE', name='workeractivitystatus'), nullable=True),
        sa.Column('status_started_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['camera_id'], ['cameras.id']),
        sa.ForeignKeyConstraint(['zone_id'], ['zones.id']),
        sa.PrimaryKeyConstraint('entity_id')
    )

    # Create table_states table
    op.create_table(
        'table_states',
        sa.Column('zone_id', sa.String(length=100), nullable=False),
        sa.Column('camera_id', sa.String(length=50), nullable=False),
        sa.Column('state', sa.Enum('FREE', 'OCCUPIED', 'DIRTY', name='tablestateenum'), nullable=True),
        sa.Column('state_started_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['camera_id'], ['cameras.id']),
        sa.ForeignKeyConstraint(['zone_id'], ['zones.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('zone_id')
    )

    # Create events_log table
    op.create_table(
        'events_log',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('entity_id', sa.String(length=50), nullable=True),
        sa.Column('role', sa.String(length=20), nullable=True),
        sa.Column('camera_id', sa.String(length=50), nullable=True),
        sa.Column('zone_id', sa.String(length=100), nullable=True),
        sa.Column('previous_state', sa.String(length=30), nullable=True),
        sa.Column('new_state', sa.String(length=30), nullable=True),
        sa.Column('event_timestamp', sa.DateTime(), nullable=False),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['camera_id'], ['cameras.id']),
        sa.ForeignKeyConstraint(['zone_id'], ['zones.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_events_log_event_type'), 'events_log', ['event_type'], unique=False)

    # Create alerts table
    op.create_table(
        'alerts',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('alert_type', sa.String(length=50), nullable=False),
        sa.Column('entity_id', sa.String(length=50), nullable=True),
        sa.Column('camera_id', sa.String(length=50), nullable=True),
        sa.Column('zone_id', sa.String(length=100), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('status', sa.Enum('ACTIVE', 'RESOLVED', name='alertstatusenum'), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['camera_id'], ['cameras.id']),
        sa.ForeignKeyConstraint(['zone_id'], ['zones.id']),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_events_log_event_type'), table_name='events_log')
    op.drop_table('alerts')
    op.drop_table('events_log')
    op.drop_table('table_states')
    op.drop_table('worker_states')
    op.drop_index(op.f('ix_customer_sessions_entity_id'), table_name='customer_sessions')
    op.drop_table('customer_sessions')
    op.drop_table('system_settings')
    op.drop_table('zones')
    op.drop_table('cameras')
    op.drop_index(op.f('ix_admins_email'), table_name='admins')
    op.drop_table('admins')
