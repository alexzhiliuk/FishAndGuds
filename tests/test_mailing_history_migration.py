from io import StringIO
from pathlib import Path
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    inspect,
    select,
)


def migration():
    return runpy.run_path(
        str(
            Path(__file__).resolve().parents[1]
            / "alembic/versions/0008_mailing_history.py"
        )
    )


def test_upgrade_preserves_existing_mailings_runs_and_downgrade():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    mailings = Table(
        "mailings",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String),
        Column("text", Text),
    )
    runs = Table(
        "mailing_runs",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("mailing_id", Integer, ForeignKey("mailings.id")),
        Column("sent_count", Integer),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            mailings.insert().values(id=1, name="Original", text="Current text")
        )
        connection.execute(runs.insert().values(id=1, mailing_id=1, sent_count=12))
        with Operations.context(MigrationContext.configure(connection)):
            migration()["upgrade"]()
        updated_runs = Table("mailing_runs", MetaData(), autoload_with=connection)
        row = connection.execute(select(updated_runs)).one()
        assert (
            row.sent_count,
            row.snapshot_text,
            row.snapshot_entities,
            row.deletion_status,
        ) == (12, None, None, "idle")
        updated_mailings = Table("mailings", MetaData(), autoload_with=connection)
        row = connection.execute(select(updated_mailings)).one()
        assert (
            row.text == "Current text"
            and row.entities is None
            and row.archived_at is None
        )
        deliveries = Table("mailing_deliveries", MetaData(), autoload_with=connection)
        connection.execute(
            deliveries.insert().values(
                run_id=1,
                telegram_id=999,
                chat_id=999,
                message_id=123,
                status="sent",
                deletion_status="pending",
            )
        )
        unique_constraints = inspect(connection).get_unique_constraints(
            "mailing_deliveries"
        )
        assert unique_constraints[0]["column_names"] == ["run_id", "telegram_id"]
        fks = inspect(connection).get_foreign_keys("mailing_deliveries")
        assert len(fks) == 1 and fks[0]["referred_table"] == "mailing_runs"
        with Operations.context(MigrationContext.configure(connection)):
            migration()["downgrade"]()
        assert connection.execute(select(runs)).one().sent_count == 12
        assert connection.execute(select(mailings)).one().text == "Current text"
        assert "mailing_deliveries" not in inspect(connection).get_table_names()
    engine.dispose()


def test_postgresql_migration_is_additive_and_records_durable_delivery_ids():
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        migration()["upgrade"]()
    sql = output.getvalue()
    assert "CREATE TABLE mailing_deliveries" in sql
    assert "message_id BIGINT" in sql and "chat_id BIGINT" in sql
    assert "UNIQUE (run_id, telegram_id)" in sql
    assert "snapshot_entities JSON" in sql
    assert "UPDATE " not in sql and "DELETE FROM " not in sql and "DROP " not in sql
