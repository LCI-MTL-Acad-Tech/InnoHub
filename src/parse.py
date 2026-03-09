"""
parse.py — extract plain text from PDF, DOCX, HTML, and plaintext files.
"""
from pathlib import Path


def parse_file(path: str | Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _parse_pdf(path)
    elif suffix in {".docx", ".doc"}:
        return _parse_docx(path)
    elif suffix in {".html", ".htm"}:
        return _parse_html(path)
    else:
        return path.read_text(errors="replace")


def _parse_pdf(path: Path) -> str:
    import fitz  # pymupdf
    doc = fitz.open(str(path))
    return "\n".join(page.get_text() for page in doc)


def _parse_docx(path: Path) -> str:
    from docx import Document
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)


def _parse_html(path: Path) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(path.read_text(errors="replace"), "lxml")
    return soup.get_text(separator="\n")
