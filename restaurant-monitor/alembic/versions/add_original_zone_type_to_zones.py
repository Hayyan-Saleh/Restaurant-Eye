"""add original_zone_type to zones

Revision ID: add_original_zone_type_to_zones
Revises: 15ed379f4248
Create Date: 2026-08-12

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_original_zone_type_to_zones'
down_revision = '15ed379f4248'
branch_labels = None
depends_on = None


def upgrade():
    # Add original_zone_type column to zones table
    # nullable=True for backward compatibility with existing data
    op.add_column('zones', sa.Column('original_zone_type', sa.String(50), nullable=True))


def downgrade():
    # Remove original_zone_type column from zones table
    op.drop_column('zones', 'original_zone_type')