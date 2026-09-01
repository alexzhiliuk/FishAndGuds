"""Add registration gender and notification channels.

Revision ID: 0003_registration_preferences
Revises: 0002_application_settings
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_registration_preferences"
down_revision = "0002_application_settings"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("gender", sa.String(16), nullable=True))
    op.add_column(
        "notification_settings",
        sa.Column(
            "sms_enabled", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
    )
    op.add_column(
        "notification_settings",
        sa.Column(
            "push_enabled", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
    )
    op.add_column(
        "notification_settings",
        sa.Column(
            "email_enabled", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
    )


def downgrade():
    op.drop_column("notification_settings", "email_enabled")
    op.drop_column("notification_settings", "push_enabled")
    op.drop_column("notification_settings", "sms_enabled")
    op.drop_column("users", "gender")
