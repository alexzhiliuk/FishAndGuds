import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.db.base import Base
from app.config import Settings


@pytest.fixture(autouse=True)
def ignore_production_env(monkeypatch):
    # Unit tests must never read credentials from the workspace's production .env.
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as value:
        yield value
    await engine.dispose()
