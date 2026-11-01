"""Token and local QR helpers for deferred guest loyalty claims."""

import base64
import hashlib
import secrets

import qrcode
from qrcode.image.svg import SvgPathImage


def create_claim_token() -> tuple[str, str]:
    """Return a new opaque token and its SHA-256 database representation."""
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    return token, token_hash


def hash_claim_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def make_claim_qr_data_uri(claim_url: str) -> str:
    """Render a QR code locally as a self-contained SVG data URI."""
    qr_code = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=4,
    )
    qr_code.add_data(claim_url)
    qr_code.make(fit=True)
    svg = qr_code.make_image(image_factory=SvgPathImage).to_string()
    encoded_svg = base64.b64encode(svg).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded_svg}"
