"""initial_schema

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-09-17 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Enable pg_trgm extension for high-performance substring/similarity searching
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    # 2. Create products table
    op.create_table(
        'products',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('sku', sa.String(length=100), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), server_default='', nullable=False),
        sa.Column('active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Case-insensitive unique index on LOWER(sku) - Authoritative database uniqueness
    op.create_index('uq_products_sku_lower', 'products', [sa.text('lower(sku)')], unique=True)
    op.create_index('ix_products_active', 'products', ['active'])
    op.create_index('ix_products_created_at', 'products', [sa.text('created_at DESC')])
    op.execute("CREATE INDEX ix_products_name_trgm ON products USING gin (name gin_trgm_ops);")
    op.execute("CREATE INDEX ix_products_sku_trgm ON products USING gin (sku gin_trgm_ops);")

    # 3. Create import_jobs table
    op.create_table(
        'import_jobs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=50), server_default='QUEUED', nullable=False),
        sa.Column('total_rows', sa.Integer(), server_default='0', nullable=False),
        sa.Column('processed_rows', sa.Integer(), server_default='0', nullable=False),
        sa.Column('successful_rows', sa.Integer(), server_default='0', nullable=False),
        sa.Column('failed_rows', sa.Integer(), server_default='0', nullable=False),
        sa.Column('progress', sa.Integer(), server_default='0', nullable=False),
        sa.Column('stage_message', sa.String(length=255), server_default='Queued', nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_import_jobs_status', 'import_jobs', ['status'])

    # 4. Create import_errors table
    op.create_table(
        'import_errors',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('import_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('row_number', sa.Integer(), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=False),
        sa.Column('raw_data', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['import_id'], ['import_jobs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_import_errors_import_id', 'import_errors', ['import_id'])

    # 5. Create webhooks table
    op.create_table(
        'webhooks',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('url', sa.String(length=2048), nullable=False),
        sa.Column('events', postgresql.ARRAY(sa.String(length=50)), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('secret', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_webhooks_enabled', 'webhooks', ['enabled'])


def downgrade() -> None:
    op.drop_table('webhooks')
    op.drop_table('import_errors')
    op.drop_table('import_jobs')
    op.drop_table('products')
    op.execute("DROP EXTENSION IF EXISTS pg_trgm;")
