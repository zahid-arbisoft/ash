from ash.app_context import build_agents
from ash.config.settings import Settings


def test_build_agents_returns_all_nodes():
    agents = build_agents(Settings())
    assert set(agents) == {"intake", "pm", "research", "coding", "reviewer", "fixer"}
    assert agents["pm"].name == "pm"
