"""Store the daily send time and the last run date of birthday greetings."""

import sqlalchemy as sa
from alembic import op

revision = "0010_birthday_send_time"
down_revision = "0009_birthday_greetings"
branch_labels = None
depends_on = None

TABLE = "birthday_greeting_settings"


def upgrade():
    op.add_column(
        TABLE,
        sa.Column("send_time", sa.Time(), server_default="10:00:00", nullable=False),
    )
    op.add_column(TABLE, sa.Column("last_run_date", sa.Date()))


def downgrade():
    op.drop_column(TABLE, "last_run_date")
    op.drop_column(TABLE, "send_time")
