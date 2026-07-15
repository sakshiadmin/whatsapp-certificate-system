"""
Calibration helper — run this LOCALLY (not part of the deployed app) to
find the correct CERTIFICATE_NAME_X / CERTIFICATE_NAME_Y values for your
specific template PDF.

Usage:
    python scripts/calibrate_certificate.py assets/certificate_template.pdf

What it does:
    1. Draws a light grid (every 50 points) with coordinate labels on top of
       your template and saves it as calibration_grid.pdf.
    2. Open calibration_grid.pdf, find where the name should sit, and read
       off the (x, y) grid coordinates near that spot.
    3. Put those numbers in your .env as CERTIFICATE_NAME_X / _Y and
       generate a real test certificate to confirm.

Note: PDF coordinates start at (0, 0) in the BOTTOM-LEFT corner of the
page, with Y increasing upward — the opposite of most image editors.
"""

import sys

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
import io


def main(template_path: str) -> None:
    reader = PdfReader(template_path)
    page = reader.pages[0]
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFont("Helvetica", 6)
    c.setStrokeColorRGB(1, 0, 0)
    c.setFillColorRGB(1, 0, 0)

    step = 50
    x = 0
    while x <= width:
        c.line(x, 0, x, height)
        c.drawString(x + 2, 4, str(x))
        x += step

    y = 0
    while y <= height:
        c.line(0, y, width, y)
        c.drawString(2, y + 2, str(y))
        y += step

    c.save()
    buf.seek(0)

    overlay = PdfReader(buf).pages[0]
    page.merge_page(overlay)

    writer = PdfWriter()
    writer.add_page(page)
    with open("calibration_grid.pdf", "wb") as f:
        writer.write(f)

    print(f"Page size: {width} x {height} points")
    print("Wrote calibration_grid.pdf — open it and read off the X/Y grid")
    print("coordinates where the student's name should be centered.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/calibrate_certificate.py <template.pdf>")
        sys.exit(1)
    main(sys.argv[1])
