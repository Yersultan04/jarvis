from __future__ import annotations

import base64
import logging
from pathlib import Path

import fitz  # PyMuPDF

from app.clients.llm import get_vision_client
from app.config import get_settings

log = logging.getLogger("ocr")


_OCR_PROMPT = (
    "Transcribe ALL visible text from this page exactly as it appears. "
    "Preserve line breaks, lists, and reading order. "
    "Do not paraphrase, summarize, or translate. "
    "If something is illegible, mark it as [illegible]. "
    "Output only the transcribed text, nothing else."
)


def _render_page_to_jpeg_b64(pdf_path: Path, page_number: int, dpi: int) -> str | None:
    """Render the 1-based page_number of pdf_path to a JPEG and return base64."""
    try:
        with fitz.open(pdf_path) as doc:
            if page_number < 1 or page_number > doc.page_count:
                return None
            page = doc.load_page(page_number - 1)
            zoom = dpi / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            jpg = pix.tobytes("jpeg")
    except Exception as e:
        log.warning("render page %s of %s failed: %s", page_number, pdf_path, e)
        return None
    return base64.b64encode(jpg).decode("ascii")


async def ocr_page(
    pdf_path: Path, page_number: int, *, model: str | None = None,
) -> str:
    """OCR a single PDF page via a vision-capable model. Returns "" on failure."""
    s = get_settings()
    b64 = _render_page_to_jpeg_b64(pdf_path, page_number, s.ingest_ocr_render_dpi)
    if b64 is None:
        return ""
    client = get_vision_client()
    try:
        resp = await client.chat.completions.create(
            model=model or s.llm_vision_model,
            temperature=0.0,
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _OCR_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                    ],
                }
            ],
        )
    except Exception as e:
        log.warning("OCR call failed for %s p.%d: %s", pdf_path.name, page_number, e)
        return ""
    return (resp.choices[0].message.content or "").strip()
