from ash.config import load_project


def test_load_plane_project():
    cfg = load_project("plane")
    assert cfg.name == "plane"
    assert cfg.issues.source_repo == "makeplane/plane"
    assert cfg.work.target_repo == "zahid-arbisoft/plane"
    assert cfg.work.mode in {"fork", "single"}


def test_runtime_dir_under_repo():
    cfg = load_project("plane")
    assert cfg.runtime_dir.name == "plane"
    assert cfg.runtime_dir.parent.name == "runtime"
