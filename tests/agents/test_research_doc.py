from ash.agents.research_doc import publish_research_doc, render_research_doc
from ash.schemas import ImplementationPlan


def _plan() -> ImplementationPlan:
    return ImplementationPlan(
        summary="Add a CSV export endpoint",
        relevant_files=["app/api.py"],
        new_files=["app/export.py"],
        steps=["wire route", "stream rows"],
        open_questions=["which delimiter?"],
    )


def test_render_includes_all_sections():
    doc = render_research_doc(_plan(), title="Export #42")
    assert "# Research — Export #42" in doc
    assert "`app/api.py`" in doc
    assert "1. wire route" in doc
    assert "which delimiter?" in doc


async def test_publish_file_mode_writes_file(tmp_path):
    ref = await publish_research_doc(
        mode="file", runtime_dir=tmp_path, run_id="r1", doc="hello"
    )
    assert ref == str(tmp_path / "research" / "r1.md")
    assert (tmp_path / "research" / "r1.md").read_text() == "hello"


async def test_publish_none_mode_skips(tmp_path):
    ref = await publish_research_doc(mode="none", runtime_dir=tmp_path, run_id="r1", doc="x")
    assert ref is None
    assert not (tmp_path / "research").exists()


async def test_publish_comment_mode_posts_to_source(tmp_path, monkeypatch):
    posted: dict = {}

    class _Provider:
        async def post_comment(self, item_id, body):
            posted.update(item_id=item_id, body=body)
            return "https://gh/issues/42#comment-1"

    async def _provider_for(cid):
        return _Provider()

    monkeypatch.setattr("ash.integrations.service.provider_for", _provider_for)
    ref = await publish_research_doc(
        mode="comment", runtime_dir=tmp_path, run_id="r1", doc="the doc",
        integration_id=7, item_id="42",
    )
    assert ref == "https://gh/issues/42#comment-1"
    assert posted == {"item_id": "42", "body": "the doc"}


async def test_publish_comment_falls_back_to_file_without_source(tmp_path):
    # comment mode but no integration/item → write a file instead of failing
    ref = await publish_research_doc(
        mode="comment", runtime_dir=tmp_path, run_id="r1", doc="x", integration_id=None, item_id=""
    )
    assert ref == str(tmp_path / "research" / "r1.md")
