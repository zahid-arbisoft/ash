"""VectorStoreClient — wraps chromadb.HttpClient for per-project semantic code search."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import chromadb

logger = logging.getLogger(__name__)

_TEXT_EXTS: frozenset[str] = frozenset(
    {".py", ".ts", ".js", ".go", ".rs", ".md", ".yaml", ".toml", ".json"}
)
_SKIP_DIRS: frozenset[str] = frozenset(
    {"node_modules", ".next", "dist", "build", "__pycache__", ".venv", "venv"}
)


class VectorStoreClient:
    """Semantic code search backed by a Chroma HTTP service (per-project collection).

    The HTTP connection is made lazily on first use so construction never raises.
    Call `ping()` to check availability before committing to indexing.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        collection: str,
        chunk_max_chars: int = 1_400,
        chunk_overlap: int = 160,
        snippet_chars: int = 900,
    ) -> None:
        self._host = host
        self._port = port
        self._name = collection
        self._http: Any = None  # connected lazily
        # Context-minimization knobs (F7): index/return small, relevant chunks — not whole files.
        self._chunk_max = max(200, chunk_max_chars)
        self._chunk_overlap = max(0, min(chunk_overlap, self._chunk_max // 2))
        self._snippet = max(120, snippet_chars)

    def _client(self) -> Any:
        if self._http is None:
            self._http = chromadb.HttpClient(host=self._host, port=self._port)
        return self._http

    def ping(self) -> bool:
        """Return True if the Chroma server is reachable, False otherwise.

        Uses list_collections() rather than heartbeat() because chromadb ≥1.0 dropped
        the v1 /heartbeat endpoint; list_collections works on both v1 and v2.
        """
        try:
            self._client().list_collections()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _collection(self) -> Any:
        return self._client().get_or_create_collection(name=self._name)

    def reset(self) -> None:
        """Wipe and recreate the collection to guarantee a fresh index."""
        try:
            self._client().delete_collection(self._name)
        except Exception:  # noqa: BLE001
            pass
        self._client().create_collection(name=self._name)

    def _chunk_file(self, rel_path: str, text: str) -> list[tuple[str, str, dict[str, Any]]]:
        """Split a file into overlapping line-windowed chunks (F7). Returns (id, doc, meta).

        Chunks carry their 1-based start/end line in metadata so search results — and the agent's
        follow-up `read_file(path, start, end)` — target the relevant span, not the whole file.
        """
        lines = text.splitlines()
        chunks: list[tuple[str, str, dict[str, Any]]] = []
        i = 0
        n = len(lines)
        while i < n:
            buf: list[str] = []
            size = 0
            start = i
            while i < n and size < self._chunk_max:
                buf.append(lines[i])
                size += len(lines[i]) + 1
                i += 1
            end = i  # exclusive
            body = "\n".join(buf).strip()
            if body:
                cid = f"{rel_path}:{start + 1}-{end}"
                chunks.append((cid, body, {"path": rel_path, "start": start + 1, "end": end}))
            if i >= n:
                break
            # step back `overlap` worth of lines so context isn't split mid-symbol
            back = 0
            j = i - 1
            while j > start and back < self._chunk_overlap:
                back += len(lines[j]) + 1
                j -= 1
            i = max(start + 1, j + 1)
        return chunks

    def _indexable_files(self, root: Path) -> Any:
        """Yield indexable files under root (text extensions, skipping vendored dirs)."""
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            if p.suffix in _TEXT_EXTS:
                yield p

    def count_indexable(self, root: Path, limit: int = 0) -> int:
        """Count indexable files under root. Stops early once `limit`+1 is reached (0 = no cap),
        so the caller can cheaply decide 'too big to embed' without a full walk."""
        root = root.resolve()
        n = 0
        for _ in self._indexable_files(root):
            n += 1
            if limit and n > limit:
                break
        return n

    def index_directory(
        self, root: Path, *, max_files: int = 0, progress_every: int = 0
    ) -> int:
        """Walk root, chunk + embed text files. Returns count of CHUNKS indexed (F7).

        `max_files` caps how many files are embedded (0 = no cap) so a huge repo can't block the
        Research agent indefinitely. `progress_every` logs every N files so it never looks hung.
        """
        col = self._collection()
        docs: list[str] = []
        ids: list[str] = []
        metas: list[dict[str, Any]] = []
        root = root.resolve()
        files = 0
        for p in self._indexable_files(root):
            if max_files and files >= max_files:
                logger.info("[chroma] hit max_files cap (%d) — stopping index", max_files)
                break
            try:
                text = p.read_bytes().decode("utf-8", errors="ignore").strip()
                if not text:
                    continue
            except OSError:
                continue
            files += 1
            rel = str(p.relative_to(root))
            for cid, body, meta in self._chunk_file(rel, text):
                ids.append(cid)
                docs.append(body)
                metas.append(meta)
            if progress_every and files % progress_every == 0:
                logger.info("[chroma] indexed %d files (%d chunks) so far", files, len(docs))
        if docs:
            col.upsert(documents=docs, ids=ids, metadatas=metas)
        return len(docs)

    def search(self, query: str, n_results: int = 10) -> list[str]:
        """Return 'path:start-end: snippet' strings for the top semantic chunk matches (F7)."""
        col = self._collection()
        results: Any = col.query(query_texts=[query], n_results=n_results)
        hits: list[str] = []
        docs_list: list[list[str]] = results.get("documents") or []
        metas_list: list[list[dict[str, Any]]] = results.get("metadatas") or []
        if docs_list and metas_list:
            for doc, meta in zip(docs_list[0], metas_list[0], strict=False):
                meta = meta or {}
                path = meta.get("path", "unknown")
                loc = f":{meta['start']}-{meta['end']}" if "start" in meta else ""
                snippet = doc[: self._snippet].replace("\n", " ")
                hits.append(f"{path}{loc}: {snippet}")
        return hits
