"""Remove Telegram notification opt-out fields.

Revision ID: 0007_remove_push_opt_out
Revises: 0006_user_data_cache
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_remove_push_opt_out"
down_revision = "0006_user_data_cache"
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        "push_enabled",
        "promotions_enabled",
        "news_enabled",
        "holidays_enabled",
    ):
        op.drop_column("notification_settings", column)


def downgrade():
    for column in (
        "promotions_enabled",
        "news_enabled",
        "holidays_enabled",
        "push_enabled",
    ):
        op.add_column(
            "notification_settings",
            sa.Column(column, sa.Boolean(), server_default=sa.true(), nullable=False),
        )
