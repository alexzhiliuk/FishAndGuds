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
            / "alembic/versions/0005_mailing_audience.py"
        )
    )


def test_audience_migration_preserves_old_runs_and_nullable_history():
    engine = create_engine("sqlite:///:memory:")
    table = Table(
        "mailing_runs",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("total_count", Integer),
    )
    table.create(engine)
    with engine.begin() as connection:
        connection.execute(table.insert().values(id=1, total_count=12))
        with Operations.context(MigrationContext.configure(connection)):
            migration()["upgrade"]()
        columns = {
            column["name"]: column
            for column in inspect(connection).get_columns("mailing_runs")
        }
        assert columns["audience_summary"]["nullable"]
        updated = Table("mailing_runs", MetaData(), autoload_with=connection)
        row = connection.execute(select(updated)).one()
        assert (row.id, row.total_count, row.audience_summary) == (1, 12, None)
        summary = dict(
            total_users=33, excluded_admins=1, excluded_inactive=1, excluded_push=19
        )
        connection.execute(
            updated.insert().values(id=2, total_count=12, audience_summary=summary)
        )
        assert (
            connection.scalar(
                select(updated.c.audience_summary).where(updated.c.id == 2)
            )
            == summary
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration()["downgrade"]()
        assert connection.execute(select(table).order_by(table.c.id)).all() == [
            (1, 12),
            (2, 12),
        ]
    engine.dispose()


def test_audience_migration_generates_postgresql_additive_ddl():
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        migration()["upgrade"]()
    assert (
        "ALTER TABLE mailing_runs ADD COLUMN audience_summary JSON;"
        in output.getvalue()
    )
    assert "UPDATE" not in output.getvalue()
