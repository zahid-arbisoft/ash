import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ash.db.base import Base
from ash.db.models import SpecRecord
from ash.db.runs import persist_spec_record, search_spec_records, update_spec_ticket_refs
from ash.schemas import Epic, Spec, TechnicalSpec, Ticket, TicketType


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    m = async_sessionmaker(engine, expire_on_commit=False)
    yield m
    await engine.dispose()


def _spec(title="CSV export", n_tickets=1, spikes=0) -> Spec:
    tickets = [
        Ticket(id=f"T{i}", title=f"t{i}", description="d", type=TicketType.feature)
        for i in range(n_tickets)
    ]
    for i in range(spikes):
        tickets.append(
            Ticket(id=f"S{i}", title="spike", description="d", type=TicketType.spike,
                   needs_research=True)
        )
    return Spec(
        epic=Epic(title=title, summary="Add export", business_goal="users", acceptance_criteria=[]),
        technical_spec=TechnicalSpec(approach="a", testing_strategy="t"),
        tickets=tickets,
    )


async def test_persist_search_and_upsert(maker, monkeypatch):
    monkeypatch.setattr("ash.db.runs.get_sessionmaker", lambda: maker)

    await persist_spec_record(
        run_id="r1", project="plane", item_id="42", intake_mode="raw_to_spec",
        spec=_spec(n_tickets=2, spikes=1), board_ref="board-1",
    )

    async with maker() as s:
        rows, total = await search_spec_records(s, query="csv")
        assert total == 1
        assert rows[0].epic_title == "CSV export"
        assert rows[0].ticket_count == 3
        assert rows[0].spike_count == 1
        # text search misses
        _, none_total = await search_spec_records(s, query="nonexistent")
        assert none_total == 0
        # project filter
        _, p_total = await search_spec_records(s, project="plane")
        assert p_total == 1

    # re-persisting the same run upserts (no duplicate row)
    await persist_spec_record(
        run_id="r1", project="plane", item_id="42", intake_mode="raw_to_spec",
        spec=_spec(title="CSV export v2", n_tickets=1),
    )
    async with maker() as s:
        rows, total = await search_spec_records(s)
        assert total == 1
        assert rows[0].epic_title == "CSV export v2"

    # ticket refs recorded after publish
    await update_spec_ticket_refs("r1", ["plane://1", "plane://2"])
    async with maker() as s:
        rec = await s.get(SpecRecord, "r1")
        assert rec is not None
        assert rec.ticket_refs == ["plane://1", "plane://2"]
