"""Convert uploaded spec files (PDF / DOCX / TXT / MD) to Markdown text.

All formats funnel into one string that the PM agent sends to the LLM. Converting to Markdown
before the LLM call strips binary/XML noise, reduces token count, and keeps a single prompt path.
"""

from __future__ import annotations

from pathlib import Path

SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}


def to_markdown(path: Path) -> str:
    """Return the file's content as a Markdown string. Raises ValueError for unsupported types."""
    ext = path.suffix.lower()
    if ext in (".md", ".txt"):
        return path.read_text(encoding="utf-8", errors="replace")
    if ext == ".pdf":
        return _pdf_to_markdown(path)
    if ext == ".docx":
        return _docx_to_markdown(path)
    raise ValueError(f"Unsupported file type: {ext!r}")


def _pdf_to_markdown(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def _docx_to_markdown(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    lines: list[str] = []
    for para in doc.paragraphs:
        style = para.style.name if para.style else ""
        text = para.text.strip()
        if not text:
            lines.append("")
            continue
        if style.startswith("Heading 1"):
            lines.append(f"# {text}")
        elif style.startswith("Heading 2"):
            lines.append(f"## {text}")
        elif style.startswith("Heading 3"):
            lines.append(f"### {text}")
        elif style.startswith("List"):
            lines.append(f"- {text}")
        else:
            # preserve inline bold/italic
            md = _runs_to_md(para)
            lines.append(md if md.strip() else text)
    return "\n".join(lines)


def _runs_to_md(para: object) -> str:
    """Convert paragraph runs to inline Markdown (bold/italic)."""
    from docx.text.paragraph import Paragraph

    assert isinstance(para, Paragraph)
    parts: list[str] = []
    for run in para.runs:
        text = run.text
        if not text:
            continue
        if run.bold and run.italic:
            text = f"***{text}***"
        elif run.bold:
            text = f"**{text}**"
        elif run.italic:
            text = f"*{text}*"
        parts.append(text)
    return "".join(parts)
