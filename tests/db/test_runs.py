import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ash.db.base import Base
from ash.db.models import RunRecord, SpecRecord
from ash.db.runs import (
    list_workbench_runs,
    persist_spec_record,
    search_spec_records,
    update_spec_ticket_refs,
)
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


async def test_list_workbench_runs_only_pm_only_enriched(maker):
    async with maker() as s:
        s.add_all(
            [
                RunRecord(run_id="wb1", project="plane", item_id="42",
                          intake_mode="raw_to_spec", pm_only=True, status="awaiting_review"),
                RunRecord(run_id="wb2", project="plane", item_id="99",
                          intake_mode="raw_to_spec", pm_only=True, status="running"),
                RunRecord(run_id="full1", project="plane", item_id="7",
                          intake_mode="raw_to_spec", pm_only=False, status="completed"),
            ]
        )
        # wb1 has a spec; wb2 is still generating (no spec)
        s.add(SpecRecord(run_id="wb1", project="plane", item_id="42",
                         epic_title="Export CSV", ticket_count=3))
        await s.commit()

        rows, total = await list_workbench_runs(s)
        assert total == 2  # full1 excluded
        ids = {r["run"].run_id for r in rows}
        assert ids == {"wb1", "wb2"}
        by_id = {r["run"].run_id: r for r in rows}
        assert by_id["wb1"]["epic_title"] == "Export CSV"
        assert by_id["wb1"]["ticket_count"] == 3
        assert by_id["wb2"]["epic_title"] is None  # no spec yet → falls back in template

        # project filter + item-id search
        _, p_total = await list_workbench_runs(s, project="plane")
        assert p_total == 2
        rows_q, q_total = await list_workbench_runs(s, query="42")
        assert q_total == 1 and rows_q[0]["run"].run_id == "wb1"
