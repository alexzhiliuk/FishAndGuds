"""Store the audience breakdown at the time of each mailing run.

Revision ID: 0005_mailing_audience
Revises: 0004_restaurant_channels
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_mailing_audience"
down_revision = "0004_restaurant_channels"
branch_labels = None
depends_on = None


def upgrade():
    # Historical runs remain NULL; their exclusion counts cannot be reconstructed.
    op.add_column(
        "mailing_runs", sa.Column("audience_summary", sa.JSON(), nullable=True)
    )


def downgrade():
    op.drop_column("mailing_runs", "audience_summary")
