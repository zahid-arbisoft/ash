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
