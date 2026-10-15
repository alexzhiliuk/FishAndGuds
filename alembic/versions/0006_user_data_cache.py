"""Track successful transaction refreshes for the on-demand user cache.

Revision ID: 0006_user_data_cache
Revises: 0005_mailing_audience
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_user_data_cache"
down_revision = "0005_mailing_audience"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "loyalty_accounts",
        sa.Column("transactions_synced_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("loyalty_accounts", "transactions_synced_at")
