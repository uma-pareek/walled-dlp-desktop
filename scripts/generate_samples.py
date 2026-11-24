"""Generate clean and sensitive sample files (TXT, PDF, PNG) for testing and demo recording."""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def _load_font(size: int):
    for font_path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arialbd.ttf",
    ):
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, size)
    return ImageFont.load_default()


def generate_samples(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Text files
    (output_dir / "clean_notes.txt").write_text(
        "Quarterly Engineering Sync Notes\n"
        "Agenda: API latency benchmarks and release checklist.\n"
        "Classification: Public internal memo.\n",
        encoding="utf-8",
    )
    (output_dir / "sensitive_notes.txt").write_text(
        "Confidential Architecture Review\n"
        "Marker: WALLED_SENSITIVE\n"
        "Do not upload to external public GenAI tools without confirmation.\n",
        encoding="utf-8",
    )

    # 2. PDF files
    def make_pdf(path: Path, lines: list) -> None:
        c = canvas.Canvas(str(path), pagesize=letter)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(72, 720, lines[0])
        c.setFont("Helvetica", 12)
        y = 690
        for line in lines[1:]:
            c.drawString(72, y, line)
            y -= 24
        c.save()

    make_pdf(
        output_dir / "clean_report.pdf",
        [
            "Public Product Overview",
            "This document contains no restricted markers.",
            "Safe for normal upload without interruption.",
        ],
    )
    make_pdf(
        output_dir / "sensitive_report.pdf",
        [
            "Internal Financial Forecast",
            "Security Tag: WALLED_SENSITIVE",
            "Upload must trigger the native desktop confirmation dialog.",
        ],
    )

    # 3. Image files (high-contrast PNGs suitable for OCR verification)
    def make_image(path: Path, heading: str, body: str) -> None:
        img = Image.new("RGB", (1100, 360), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        font_lg = _load_font(44)
        font_md = _load_font(36)
        draw.text((60, 70), heading, fill=(15, 23, 42), font=font_lg)
        draw.text((60, 175), body, fill=(15, 23, 42), font=font_md)
        img.save(path, format="PNG")

    make_image(
        output_dir / "clean_screenshot.png",
        "PUBLIC SYSTEM DIAGRAM",
        "Status: Healthy - No restricted markers",
    )
    make_image(
        output_dir / "sensitive_screenshot.png",
        "CONFIDENTIAL SCREENSHOT",
        "TAG: WALLED_SENSITIVE",
    )


if __name__ == "__main__":
    base = Path(__file__).resolve().parent.parent / "samples"
    generate_samples(base)
    print(f"Generated sample fixtures in {base}")
