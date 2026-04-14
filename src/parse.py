"""
parse.py — extract plain text from PDF, DOCX, HTML, image, and plaintext files.

Supported formats:
  .pdf            — text extraction via PyMuPDF; falls back to OCR if text yield is low
  .docx / .doc    — python-docx
  .html / .htm    — BeautifulSoup
  .png / .jpg /
  .jpeg / .tiff /
  .bmp / .webp    — Tesseract OCR (eng+fra)
  anything else   — read as plain text

OCR is also used as a fallback for image-only / scanned PDFs where PyMuPDF
extracts fewer than MIN_PDF_CHARS characters per page.
"""
from pathlib import Path

# Images that OCR can handle
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp", ".gif"}

# If a PDF yields fewer characters per page than this, assume it is image-based
# and re-run it through OCR
_MIN_PDF_CHARS_PER_PAGE = 30


def parse_file(path: str | Path) -> str:
    path   = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        return _parse_pdf(path)
    elif suffix in {".docx", ".doc"}:
        return _parse_docx(path)
    elif suffix in {".html", ".htm"}:
        return _parse_html(path)
    elif suffix in _IMAGE_EXTENSIONS:
        return _ocr_image(path)
    else:
        return path.read_text(errors="replace")


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_pdf(path: Path) -> str:
    import fitz  # pymupdf
    doc   = fitz.open(str(path))
    pages = [page.get_text() for page in doc]
    text  = "\n".join(pages).strip()

    # If the PDF is image-only (scanned), text will be near-empty
    n_pages = max(len(pages), 1)
    if len(text) / n_pages < _MIN_PDF_CHARS_PER_PAGE:
        text = _ocr_pdf(path, doc)

    doc.close()
    return text


def _ocr_pdf(path: Path, doc) -> str:
    """Render each PDF page as an image and run Tesseract on it."""
    import fitz
    from PIL import Image
    import pytesseract, io

    parts = []
    for page in doc:
        # Render at 200 DPI for a reasonable quality/speed tradeoff
        mat  = fitz.Matrix(200 / 72, 200 / 72)
        pix  = page.get_pixmap(matrix=mat)
        img  = Image.open(io.BytesIO(pix.tobytes("png")))
        parts.append(pytesseract.image_to_string(img, lang="fra+eng"))
    return "\n".join(parts)


def _ocr_image(path: Path) -> str:
    """Run Tesseract OCR on a standalone image file."""
    from PIL import Image
    import pytesseract
    img = Image.open(str(path))
    # Convert to RGB if needed (e.g. RGBA PNG, palette mode)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return pytesseract.image_to_string(img, lang="fra+eng")


def _parse_docx(path: Path) -> str:
    from docx import Document
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)


def _parse_html(path: Path) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(path.read_text(errors="replace"), "lxml")
    return soup.get_text(separator="\n")
