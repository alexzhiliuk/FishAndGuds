from io import StringIO
from pathlib import Path
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, inspect, select


def migration():
    return runpy.run_path(
        str(
            Path(__file__).resolve().parents[1]
            / "alembic/versions/0006_user_data_cache.py"
        )
    )


def test_cache_migration_preserves_existing_account_and_revision():
    engine = create_engine("sqlite:///:memory:")
    table = Table(
        "loyalty_accounts",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("last_transaction_revision", Integer),
    )
    table.create(engine)
    with engine.begin() as conn:
        conn.execute(table.insert().values(id=1, last_transaction_revision=123))
        with Operations.context(MigrationContext.configure(conn)):
            migration()["upgrade"]()
        columns = {
            column["name"]: column
            for column in inspect(conn).get_columns("loyalty_accounts")
        }
        assert columns["transactions_synced_at"]["nullable"]
        updated = Table("loyalty_accounts", MetaData(), autoload_with=conn)
        row = conn.execute(select(updated)).one()
        assert (row.id, row.last_transaction_revision, row.transactions_synced_at) == (
            1,
            123,
            None,
        )
        with Operations.context(MigrationContext.configure(conn)):
            migration()["downgrade"]()
        assert conn.execute(select(table)).all() == [(1, 123)]
    engine.dispose()


def test_cache_migration_generates_postgresql_timestamp_ddl():
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        migration()["upgrade"]()
    assert (
        "ADD COLUMN transactions_synced_at TIMESTAMP WITH TIME ZONE"
        in output.getvalue()
    )
    assert "UPDATE" not in output.getvalue()
