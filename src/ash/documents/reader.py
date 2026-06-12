"""Read uploaded spec files (pdf / docx / md / txt / …) into plain text.

Routes by extension to the matching LangChain community document loader, so the PM agent can ingest
requirements from real documents, not just issue text. Loaders are imported lazily so the optional
parsers (pypdf, docx2txt) are only needed when those formats are actually used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_TEXT_EXTS = {".md", ".markdown", ".txt", ".rst", ".text"}


def read_document(path: str | Path) -> str:
    """Return the text of one document; unknown types fall back to a best-effort text read."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"attachment not found: {p}")
    ext = p.suffix.lower()

    if ext in _TEXT_EXTS:
        return p.read_text(errors="ignore")
    if ext == ".pdf":
        return _load(p, "langchain_community.document_loaders", "PyPDFLoader")
    if ext in {".docx", ".doc"}:
        return _load(p, "langchain_community.document_loaders", "Docx2txtLoader")
    if ext in {".html", ".htm"}:
        return _load(p, "langchain_community.document_loaders", "BSHTMLLoader")
    # unknown extension: try plain text, never hard-fail the run
    try:
        return p.read_text(errors="ignore")
    except OSError:
        return ""


def read_documents(paths: list[str] | list[Path]) -> str:
    """Read several documents and concatenate them with per-file headers."""
    chunks: list[str] = []
    for path in paths:
        name = Path(path).name
        chunks.append(f"### {name}\n\n{read_document(path).strip()}")
    return "\n\n---\n\n".join(chunks)


def _load(path: Path, module: str, loader_name: str) -> str:
    import importlib

    loader_cls: Any = getattr(importlib.import_module(module), loader_name)
    docs = loader_cls(str(path)).load()
    return "\n\n".join(d.page_content for d in docs)
