import pytest

from ash.db.models import Connector, ConnectorKind
from ash.integrations.service import validate_connector


def _c(**kw) -> Connector:
    base = {"name": "x", "kind": ConnectorKind.github}
    base.update(kw)
    return Connector(**base)


def test_valid_github_source_has_no_issues():
    assert validate_connector(_c(kind=ConnectorKind.github, is_source=True)) == []


def test_valid_file_sink_has_no_issues():
    assert validate_connector(_c(kind=ConnectorKind.file, is_sink=True)) == []


def test_default_sink_must_be_sink():
    issues = validate_connector(_c(kind=ConnectorKind.jira, is_default_sink=True, is_sink=False))
    assert any("default sink" in i for i in issues)


def test_mcp_requires_base_url():
    issues = validate_connector(
        _c(kind=ConnectorKind.github, transport="http", base_url=None, is_source=True)
    )
    assert any("base_url" in i for i in issues)
    # with a base_url it's fine
    assert validate_connector(
        _c(kind=ConnectorKind.github, transport="http", base_url="https://mcp", is_source=True)
    ) == []


def test_file_cannot_be_source():
    issues = validate_connector(_c(kind=ConnectorKind.file, is_source=True))
    assert any("issue source" in i for i in issues)


def test_github_cannot_be_sink():
    issues = validate_connector(_c(kind=ConnectorKind.github, is_sink=True))
    assert any("ticket sink" in i for i in issues)


async def test_create_connector_rejects_incoherent(monkeypatch):
    """create_connector raises on an invalid combo (default sink that isn't a sink)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from ash.db.base import Base
    from ash.integrations.service import create_connector

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        with pytest.raises(ValueError, match="default sink"):
            await create_connector(
                s, name="bad", kind=ConnectorKind.jira, is_default_sink=True, is_sink=False
            )
    await engine.dispose()
