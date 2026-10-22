"""Preserve mailing content, individual deliveries, and archived templates."""

from alembic import op
import sqlalchemy as sa

revision = "0008_mailing_history"
down_revision = "0007_remove_push_opt_out"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("mailings", sa.Column("entities", sa.JSON(), nullable=True))
    op.add_column(
        "mailings", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_mailings_archived_at", "mailings", ["archived_at"])
    for column in (
        sa.Column("snapshot_name", sa.String(200), nullable=True),
        sa.Column("snapshot_text", sa.Text(), nullable=True),
        sa.Column("snapshot_entities", sa.JSON(), nullable=True),
        sa.Column("snapshot_image_file_id", sa.String(500), nullable=True),
        sa.Column(
            "deletion_status", sa.String(20), server_default="idle", nullable=False
        ),
    ):
        op.add_column("mailing_runs", column)
    op.create_table(
        "mailing_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("mailing_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("deletion_status", sa.String(20), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletion_error", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "run_id", "telegram_id", name="uq_mailing_delivery_recipient"
        ),
    )
    op.create_index("ix_mailing_deliveries_run_id", "mailing_deliveries", ["run_id"])


def downgrade():
    op.drop_table("mailing_deliveries")
    for column in (
        "deletion_status",
        "snapshot_image_file_id",
        "snapshot_entities",
        "snapshot_text",
        "snapshot_name",
    ):
        op.drop_column("mailing_runs", column)
    op.drop_index("ix_mailings_archived_at", table_name="mailings")
    op.drop_column("mailings", "archived_at")
    op.drop_column("mailings", "entities")
