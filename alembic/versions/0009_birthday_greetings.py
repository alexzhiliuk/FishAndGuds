"""Store the birthday greeting configured in the Telegram admin panel."""

import sqlalchemy as sa
from alembic import op

revision = "0009_birthday_greetings"
down_revision = "0008_mailing_history"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "birthday_greeting_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("text", sa.Text()),
        sa.Column("entities", sa.JSON()),
        sa.Column("image_file_id", sa.String(500)),
        sa.CheckConstraint("id = 1", name="singleton"),
    )


def downgrade():
    op.drop_table("birthday_greeting_settings")
