from ash.documents.reader import read_document, read_documents


def test_reads_markdown_and_text(tmp_path):
    md = tmp_path / "spec.md"
    md.write_text("# Title\n\nbody")
    assert "body" in read_document(md)

    txt = tmp_path / "notes.txt"
    txt.write_text("plain")
    assert read_document(txt) == "plain"


def test_unknown_extension_falls_back_to_text(tmp_path):
    f = tmp_path / "weird.xyz"
    f.write_text("still readable")
    assert read_document(f) == "still readable"


def test_read_documents_concatenates_with_headers(tmp_path):
    a = tmp_path / "a.md"
    a.write_text("alpha")
    b = tmp_path / "b.txt"
    b.write_text("beta")
    combined = read_documents([a, b])
    assert "### a.md" in combined
    assert "alpha" in combined
    assert "### b.txt" in combined
    assert "beta" in combined


def test_missing_file_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        read_document(tmp_path / "nope.md")
