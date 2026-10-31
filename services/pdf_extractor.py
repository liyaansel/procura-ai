"""Safe, page-preserving PDF text extraction."""

from typing import Any

import pymupdf

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_EXTRACTED_CHARACTERS = 250_000


class PDFExtractionError(ValueError):
    """A proposal cannot be safely extracted for evaluation."""


def extract_pdf(filename: str, content: bytes) -> list[dict[str, Any]]:
    if not filename.lower().endswith(".pdf"):
        raise PDFExtractionError(f"{filename}: only PDF files are supported.")
    if not content:
        raise PDFExtractionError(f"{filename}: the uploaded PDF is empty.")
    if len(content) > MAX_PDF_BYTES:
        raise PDFExtractionError(f"{filename}: file exceeds the 20 MB limit.")
    if not content.startswith(b"%PDF-"):
        raise PDFExtractionError(f"{filename}: the file is not a valid PDF.")
    try:
        document = pymupdf.open(stream=content, filetype="pdf")
        pages = [
            {"page_number": index + 1, "text": page.get_text("text").strip()}
            for index, page in enumerate(document)
        ]
    except (pymupdf.FileDataError, pymupdf.EmptyFileError, RuntimeError) as exc:
        raise PDFExtractionError(f"{filename}: could not read this PDF.") from exc
    finally:
        if "document" in locals():
            document.close()
    if not pages or not any(page["text"] for page in pages):
        raise PDFExtractionError(
            f"{filename}: no readable text found; scanned/image-only PDFs require OCR, which is not supported."
        )
    total = sum(len(page["text"]) for page in pages)
    if total > MAX_EXTRACTED_CHARACTERS:
        raise PDFExtractionError(
            f"{filename}: extracted text exceeds the {MAX_EXTRACTED_CHARACTERS:,}-character limit."
        )
    return pages
