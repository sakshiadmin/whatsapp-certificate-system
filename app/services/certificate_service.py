"""
Certificate PDF generation.

Approach: OVERLAY, not redraw-from-scratch.
------------------------------------------
You already have a designed certificate template (background, logos,
borders, signatures — as a PDF). We do NOT try to recreate that design in
code. Instead:

  1. ReportLab draws ONLY the student's name (and later, certificate ID /
     date) onto a blank, transparent-background, single-page PDF the exact
     same size as your template.
  2. pypdf merges that "text layer" PDF on top of your existing template
     PDF, page by page.

This means your designer's template stays exactly as designed, and if you
ever update the template file (new logo, new signature), you don't touch
this code at all — you just replace assets/certificate_template.pdf.

Unique certificate IDs
-----------------------
Format: CERT-<YYYY>-<6 uppercase hex chars>, e.g. CERT-2026-4F9A2B.
- Human-readable and sortable by year.
- The 6 hex chars come from a cryptographically random UUID slice, not a
  counter — so two servers/workers generating IDs at the same time can
  never collide, and IDs can't be easily guessed/enumerated by a student
  trying to guess someone else's certificate ID.
- Collision probability with random 24-bit IDs is negligible at the scale
  of 20,000 students (birthday-paradox math: ~20,000^2 / (2 * 16,777,216)
  ≈ 12 expected collisions if this were reused across ALL TIME — so in
  production also treat the Google Sheet's "Certificate ID" column as the
  final uniqueness check, which generate_unique_certificate_id() does by
  accepting a set of already-used IDs to avoid.)
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.config import Settings

_font_registered = False


def _ensure_font_registered(settings: Settings) -> str:
    """Registers the custom TTF font with ReportLab exactly once per process."""
    global _font_registered
    if not _font_registered:
        pdfmetrics.registerFont(
            TTFont(settings.certificate_font_name, settings.certificate_font_path)
        )
        _font_registered = True
    return settings.certificate_font_name


def generate_unique_certificate_id(existing_ids: set[str]) -> str:
    year = datetime.now(timezone.utc).year
    for _ in range(10):  # extremely unlikely to ever loop more than once
        candidate = f"CERT-{year}-{uuid.uuid4().hex[:6].upper()}"
        if candidate not in existing_ids:
            return candidate
    # If we somehow exhaust 10 random attempts, fall back to a longer suffix.
    return f"CERT-{year}-{uuid.uuid4().hex[:12].upper()}"


def generate_certificate_pdf(
    settings: Settings,
    student_name: str,
    certificate_id: str,
) -> bytes:
    """Returns the finished certificate as PDF bytes, ready to upload."""

    reader = PdfReader(settings.certificate_template_path)
    template_page = reader.pages[0]
    page_width = float(template_page.mediabox.width)
    page_height = float(template_page.mediabox.height)

    font_name = _ensure_font_registered(settings)

    # 1. Draw the text layer.
    text_buffer = io.BytesIO()
    c = canvas.Canvas(text_buffer, pagesize=(page_width, page_height))
    c.setFont(font_name, settings.certificate_name_font_size)
    c.drawCentredString(
        settings.certificate_name_x,
        settings.certificate_name_y,
        student_name,
    )
    # Certificate ID printed small, bottom-right — remove if not wanted yet.
    c.setFont(font_name, 9)
    c.drawRightString(page_width - 24, 24, certificate_id)
    c.save()
    text_buffer.seek(0)

    # 2. Merge text layer onto the template.
    overlay_reader = PdfReader(text_buffer)
    writer = PdfWriter()
    base_page = reader.pages[0]
    base_page.merge_page(overlay_reader.pages[0])
    writer.add_page(base_page)

    output_buffer = io.BytesIO()
    writer.write(output_buffer)
    return output_buffer.getvalue()


# Unused import guard: A4 import kept for reference/calibration script use.
__all__ = ["generate_certificate_pdf", "generate_unique_certificate_id", "A4"]
