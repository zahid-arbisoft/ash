from ash.config import ProjectConfig, load_project


def test_load_plane_project():
    cfg = load_project("plane")
    assert cfg.name == "plane"
    assert cfg.issues is not None
    assert cfg.issues.source_repo == "makeplane/plane"
    assert cfg.work is not None
    assert cfg.work.target_repo == "zahid-arbisoft/plane"
    assert cfg.work.mode in {"fork", "single"}


def test_minimal_project_needs_only_a_name():
    # a PM-only / attachments run needs no issue source or work target
    cfg = ProjectConfig.model_validate({"name": "docpm"})
    assert cfg.issues is None
    assert cfg.work is None
    assert cfg.runtime_dir.name == "docpm"


def test_runtime_dir_under_repo():
    cfg = load_project("plane")
    assert cfg.runtime_dir.name == "plane"
    assert cfg.runtime_dir.parent.name == "runtime"
