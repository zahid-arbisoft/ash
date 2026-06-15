"""VectorStoreClient — wraps chromadb.HttpClient for per-project semantic code search."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb

_TEXT_EXTS: frozenset[str] = frozenset(
    {".py", ".ts", ".js", ".go", ".rs", ".md", ".yaml", ".toml", ".json"}
)
_SKIP_DIRS: frozenset[str] = frozenset(
    {"node_modules", ".next", "dist", "build", "__pycache__", ".venv", "venv"}
)


class VectorStoreClient:
    """Semantic code search backed by a Chroma HTTP service (per-project collection)."""

    def __init__(self, *, host: str, port: int, collection: str) -> None:
        self._http: Any = chromadb.HttpClient(host=host, port=port)
        self._name = collection

    def _collection(self) -> Any:
        return self._http.get_or_create_collection(name=self._name)

    def reset(self) -> None:
        """Wipe and recreate the collection to guarantee a fresh index."""
        try:
            self._http.delete_collection(self._name)
        except Exception:  # noqa: BLE001
            pass
        self._http.create_collection(name=self._name)

    def index_directory(self, root: Path) -> int:
        """Walk root, embed and store text files. Returns count of files indexed."""
        col = self._collection()
        docs: list[str] = []
        ids: list[str] = []
        metas: list[dict[str, str]] = []
        count = 0
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            if p.suffix not in _TEXT_EXTS:
                continue
            try:
                text = p.read_bytes().decode("utf-8", errors="ignore").strip()
                if not text:
                    continue
            except OSError:
                continue
            docs.append(text)
            ids.append(str(p))
            metas.append({"path": str(p)})
            count += 1
        if docs:
            col.upsert(documents=docs, ids=ids, metadatas=metas)
        return count

    def search(self, query: str, n_results: int = 10) -> list[str]:
        """Return 'path: snippet' strings for the top semantic matches."""
        col = self._collection()
        results: Any = col.query(query_texts=[query], n_results=n_results)
        hits: list[str] = []
        docs_list: list[list[str]] = results.get("documents") or []
        metas_list: list[list[dict[str, Any]]] = results.get("metadatas") or []
        if docs_list and metas_list:
            for doc, meta in zip(docs_list[0], metas_list[0], strict=False):
                path = (meta or {}).get("path", "unknown")
                snippet = doc[:300].replace("\n", " ")
                hits.append(f"{path}: {snippet}")
        return hits
