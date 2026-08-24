"""Add editable application settings.

Revision ID: 0002_application_settings
Revises: 0001_initial
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_application_settings"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "application_settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table("application_settings")
