from __future__ import annotations

import io

import qrcode


def make_qr_png(data: str) -> io.BytesIO:
    """Render `data` into a PNG QR code returned as an in-memory buffer."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    buf.name = "config.png"
    return buf
