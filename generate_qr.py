#!/usr/bin/env python3
"""
Generate a printable QR code for the wedding upload page.

Usage:
    python generate_qr.py
    python generate_qr.py https://yourdomain.com
"""

import sys
from pathlib import Path

try:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Install dependencies first:  pip install qrcode[pil] Pillow")
    sys.exit(1)


def make_qr(url: str, output: str = "wedding_qr.png") -> None:
    # ERROR_CORRECT_M = ~15% damage tolerance — good balance of density vs. reliability
    qr = qrcode.QRCode(
        version=None,            # auto-size
        error_correction=ERROR_CORRECT_M,
        box_size=12,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)

    qr_img = qr.make_image(fill_color="#333333", back_color="white").convert("RGB")

    # Add a small caption below the QR so printed copies are self-explanatory
    w, h = qr_img.size
    caption_height = 60
    final = Image.new("RGB", (w, h + caption_height), "white")
    final.paste(qr_img, (0, 0))

    draw = ImageDraw.Draw(final)
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except OSError:
        font = ImageFont.load_default()

    lines = []
    y = h + 6
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((w - tw) // 2, y), line, fill="#444444", font=font)
        y += 22

    final.save(output)
    print(f"[OK] QR code saved to: {Path(output).resolve()}")
    print(f"  URL: {url}")
    print("  Print it and display it at the wedding table!")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target_url = sys.argv[1]
    else:
        target_url = input("Enter the upload URL (e.g. https://yourdomain.com): ").strip()
        if not target_url:
            print("No URL provided. Exiting.")
            sys.exit(1)

    make_qr(target_url)
