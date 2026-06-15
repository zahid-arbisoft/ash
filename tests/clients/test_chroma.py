"""Tests for VectorStoreClient — chromadb.HttpClient mocked at the boundary."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ash.clients.chroma import VectorStoreClient


@pytest.fixture
def mock_http() -> MagicMock:
    with patch("ash.clients.chroma.chromadb.HttpClient") as mock_cls:
        yield mock_cls.return_value


def test_reset_deletes_and_recreates_collection(mock_http: MagicMock) -> None:
    client = VectorStoreClient(host="localhost", port=8001, collection="test")
    client.reset()
    mock_http.delete_collection.assert_called_once_with("test")
    mock_http.create_collection.assert_called_once_with(name="test")


def test_reset_swallows_delete_error(mock_http: MagicMock) -> None:
    mock_http.delete_collection.side_effect = Exception("not found")
    client = VectorStoreClient(host="localhost", port=8001, collection="test")
    client.reset()  # must not raise
    mock_http.create_collection.assert_called_once()


def test_index_directory_indexes_text_files(tmp_path: Path, mock_http: MagicMock) -> None:
    (tmp_path / "hello.py").write_text("print('hello')")
    (tmp_path / "readme.md").write_text("# readme")
    col = MagicMock()
    mock_http.get_or_create_collection.return_value = col

    client = VectorStoreClient(host="localhost", port=8001, collection="test")
    count = client.index_directory(tmp_path)

    assert count == 2
    col.upsert.assert_called_once()
    call_kwargs = col.upsert.call_args.kwargs
    assert len(call_kwargs["documents"]) == 2


def test_index_directory_skips_binary_extensions(tmp_path: Path, mock_http: MagicMock) -> None:
    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / "b.exe").write_bytes(b"\x00\x01\x02")
    col = MagicMock()
    mock_http.get_or_create_collection.return_value = col

    client = VectorStoreClient(host="localhost", port=8001, collection="test")
    count = client.index_directory(tmp_path)

    assert count == 1


def test_index_directory_empty_dir_skips_upsert(tmp_path: Path, mock_http: MagicMock) -> None:
    col = MagicMock()
    mock_http.get_or_create_collection.return_value = col

    client = VectorStoreClient(host="localhost", port=8001, collection="test")
    count = client.index_directory(tmp_path)

    assert count == 0
    col.upsert.assert_not_called()


def test_search_returns_path_snippet_pairs(mock_http: MagicMock) -> None:
    col = MagicMock()
    col.query.return_value = {
        "documents": [["def foo(): pass"]],
        "metadatas": [[{"path": "/repo/foo.py"}]],
    }
    mock_http.get_or_create_collection.return_value = col

    client = VectorStoreClient(host="localhost", port=8001, collection="test")
    hits = client.search("foo function")

    assert len(hits) == 1
    assert "/repo/foo.py" in hits[0]
    assert "def foo" in hits[0]


def test_search_empty_results(mock_http: MagicMock) -> None:
    col = MagicMock()
    col.query.return_value = {"documents": [[]], "metadatas": [[]]}
    mock_http.get_or_create_collection.return_value = col

    client = VectorStoreClient(host="localhost", port=8001, collection="test")
    hits = client.search("nonexistent")

    assert hits == []


def test_chunking_splits_large_file_with_line_ranges(tmp_path: Path, mock_http: MagicMock) -> None:
    # A file bigger than one chunk produces several chunks, each carrying a line range (F7).
    body = "\n".join(f"line {i}" for i in range(1, 401))  # ~400 lines
    (tmp_path / "big.py").write_text(body)
    col = MagicMock()
    mock_http.get_or_create_collection.return_value = col

    client = VectorStoreClient(
        host="localhost", port=8001, collection="t", chunk_max_chars=400, chunk_overlap=40
    )
    count = client.index_directory(tmp_path)

    assert count > 1  # split into multiple chunks
    ids = col.upsert.call_args.kwargs["ids"]
    metas = col.upsert.call_args.kwargs["metadatas"]
    assert all(":" in cid for cid in ids)  # ids are path:start-end
    assert all("start" in m and "end" in m for m in metas)


def test_count_indexable_stops_at_limit(tmp_path: Path, mock_http: MagicMock) -> None:
    for i in range(10):
        (tmp_path / f"f{i}.py").write_text("x = 1")
    client = VectorStoreClient(host="localhost", port=8001, collection="t")
    # limit=3 → stops early, returns limit+1 to signal "more than limit"
    assert client.count_indexable(tmp_path, limit=3) == 4
    assert client.count_indexable(tmp_path, limit=0) == 10  # no cap → exact count


def test_index_directory_respects_max_files(tmp_path: Path, mock_http: MagicMock) -> None:
    for i in range(10):
        (tmp_path / f"f{i}.py").write_text("x = 1\ny = 2")
    col = MagicMock()
    mock_http.get_or_create_collection.return_value = col
    client = VectorStoreClient(host="localhost", port=8001, collection="t")
    client.index_directory(tmp_path, max_files=4)
    # only 4 files embedded → ≤ 4 chunks (these tiny files = 1 chunk each)
    ids = col.upsert.call_args.kwargs["ids"]
    assert len({cid.split(":")[0] for cid in ids}) == 4


def test_search_includes_line_range(mock_http: MagicMock) -> None:
    col = MagicMock()
    col.query.return_value = {
        "documents": [["def foo(): pass"]],
        "metadatas": [[{"path": "foo.py", "start": 10, "end": 20}]],
    }
    mock_http.get_or_create_collection.return_value = col
    client = VectorStoreClient(host="localhost", port=8001, collection="t")
    hits = client.search("foo")
    assert "foo.py:10-20" in hits[0]
