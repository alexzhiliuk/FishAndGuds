"""Create the current loyalty bot schema."""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sync_status = sa.Enum("pending", "synced", "error", name="iikosyncstatus")
    mailing_type = sa.Enum(
        "manual", "scheduled", "birthday", "holiday", name="mailingtype"
    )
    mailing_status = sa.Enum(
        "draft",
        "scheduled",
        "sending",
        "sent",
        "cancelled",
        "failed",
        name="mailingstatus",
    )
    run_status = sa.Enum("sending", "sent", "failed", name="runstatus")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=True),
        sa.Column("middle_name", sa.String(100), nullable=True),
        sa.Column("birthday", sa.Date(), nullable=True),
        sa.Column("email", sa.String(254), nullable=True),
        sa.Column(
            "personal_data_consent_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"], unique=True)
    op.create_index("ix_users_phone", "users", ["phone"], unique=True)

    op.create_table(
        "restaurants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("iiko_organization_id", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("address", sa.String(300), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("code", sa.String(100), nullable=True),
        sa.Column("inn", sa.String(32), nullable=True),
        sa.Column("delivery_url", sa.String(500), nullable=True),
        sa.Column("reviews_url", sa.String(500), nullable=True),
        sa.Column("website_url", sa.String(500), nullable=True),
        sa.Column("image_name", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "mailings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("image_file_id", sa.String(500), nullable=True),
        sa.Column("type", mailing_type, server_default="manual", nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", mailing_status, server_default="draft", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_mailings_scheduled_at", "mailings", ["scheduled_at"])
    op.create_index("ix_mailings_status", "mailings", ["status"])

    op.create_table(
        "loyalty_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("iiko_customer_id", sa.String(128), nullable=True, unique=True),
        sa.Column("iiko_card_id", sa.String(128), nullable=True),
        sa.Column("card_number", sa.String(64), nullable=True, unique=True),
        sa.Column("card_track", sa.String(128), nullable=True),
        sa.Column("qr_payload", sa.String(256), nullable=True),
        sa.Column(
            "categories",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
        sa.Column(
            "last_known_balance", sa.Numeric(12, 2), server_default="0", nullable=False
        ),
        sa.Column("balance_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_transaction_revision", sa.BigInteger(), nullable=True),
        sa.Column("last_transaction_id", sa.String(128), nullable=True),
        sa.Column(
            "iiko_sync_status", sync_status, server_default="pending", nullable=False
        ),
        sa.Column("iiko_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_loyalty_accounts_iiko_sync_status", "loyalty_accounts", ["iiko_sync_status"]
    )

    op.create_table(
        "notification_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "promotions_enabled", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column(
            "news_enabled", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column(
            "holidays_enabled", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "loyalty_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(128), nullable=False, unique=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("wallet_id", sa.String(128), nullable=True),
        sa.Column("program_id", sa.String(128), nullable=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("type_name", sa.String(200), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("balance_before", sa.Numeric(12, 2), nullable=True),
        sa.Column("balance_after", sa.Numeric(12, 2), nullable=True),
        sa.Column("order_number", sa.String(64), nullable=True),
        sa.Column("order_sum", sa.Numeric(12, 2), nullable=True),
        sa.Column("pos_order_id", sa.String(128), nullable=True),
        sa.Column("organization_id", sa.String(128), nullable=True),
        sa.Column("terminal_group_id", sa.String(128), nullable=True),
        sa.Column("is_delivery", sa.Boolean(), nullable=True),
        sa.Column(
            "is_ignored", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("created_at_iiko", sa.DateTime(timezone=True), nullable=False),
        sa.Column("when_created_order", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_loyalty_transactions_user_id", "loyalty_transactions", ["user_id"]
    )
    op.create_index(
        "ix_loyalty_transactions_pos_order_id", "loyalty_transactions", ["pos_order_id"]
    )
    op.create_index(
        "ix_loyalty_transactions_created_at_iiko",
        "loyalty_transactions",
        ["created_at_iiko"],
    )

    op.create_table(
        "purchases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "restaurant_id",
            sa.Integer(),
            sa.ForeignKey("restaurants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("pos_order_id", sa.String(128), nullable=False, unique=True),
        sa.Column("order_number", sa.String(64), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column(
            "bonus_earned", sa.Numeric(12, 2), server_default="0", nullable=False
        ),
        sa.Column("bonus_spent", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_delivery", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_purchases_user_id", "purchases", ["user_id"])
    op.create_index("ix_purchases_purchased_at", "purchases", ["purchased_at"])

    op.create_table(
        "mailing_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "mailing_id",
            sa.Integer(),
            sa.ForeignKey("mailings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("total_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sent_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", run_status, server_default="sending", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_mailing_runs_mailing_id", "mailing_runs", ["mailing_id"])


def downgrade():
    for table in (
        "mailing_runs",
        "purchases",
        "loyalty_transactions",
        "notification_settings",
        "loyalty_accounts",
        "mailings",
        "restaurants",
        "users",
    ):
        op.drop_table(table)
    for enum_name in ("runstatus", "mailingstatus", "mailingtype", "iikosyncstatus"):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
