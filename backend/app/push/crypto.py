import base64
import hashlib
import json
import time
from urllib.parse import urlparse

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    PrivateFormat,
    NoEncryption,
    load_pem_private_key,
)


def urlsafe_b64encode(data: bytes) -> str:
    """Encode bytes using URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def urlsafe_b64decode(s: str) -> bytes:
    """Decode URL-safe base64 string with optional padding."""
    pad = len(s) % 4
    if pad:
        s += "=" * (4 - pad)
    return base64.urlsafe_b64decode(s.encode("ascii"))


def sanitize_endpoint(endpoint: str) -> str:
    """Mask push endpoint in logs to prevent leaking subscriber endpoints."""
    try:
        parsed = urlparse(endpoint)
        path_hash = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:12]
        return f"{parsed.scheme}://{parsed.netloc}/...[hash:{path_hash}]"
    except Exception:
        return "[sanitized_endpoint]"


class VapidKeyManager:
    """Generates and manages VAPID (Voluntary Application Server Identification) EC keys for Web Push."""

    def __init__(self, private_key_pem: str | None = None, public_key_b64: str | None = None):
        self._private_key: ec.EllipticCurvePrivateKey | None = None
        self._public_key_b64: str | None = public_key_b64

        if private_key_pem and private_key_pem.strip():
            try:
                self._private_key = load_pem_private_key(
                    private_key_pem.encode("utf-8"), password=None
                )
            except Exception:
                pass

        if self._private_key is None:
            # Generate deterministic or transient key pair for dev/testing
            self._private_key = ec.generate_private_key(ec.SECP256R1())

        if not self._public_key_b64:
            public_bytes = self._private_key.public_key().public_bytes(
                encoding=Encoding.X962,
                format=PublicFormat.UncompressedPoint,
            )
            self._public_key_b64 = urlsafe_b64encode(public_bytes)

    @property
    def public_key_b64(self) -> str:
        return self._public_key_b64 or ""

    def export_private_key_pem(self) -> str:
        return self._private_key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.PKCS8,
            encryption_algorithm=NoEncryption(),
        ).decode("utf-8")

    def create_vapid_headers(self, endpoint: str, subscriber: str = "mailto:admin@macanaliz.com") -> dict[str, str]:
        """Create RFC 8292 VAPID Authorization header."""
        parsed = urlparse(endpoint)
        audience = f"{parsed.scheme}://{parsed.netloc}"
        now = int(time.time())

        header = {"alg": "ES256", "typ": "JWT"}
        claims = {
            "aud": audience,
            "exp": now + 86400,
            "sub": subscriber,
        }

        header_b64 = urlsafe_b64encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        claims_b64 = urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{header_b64}.{claims_b64}".encode("utf-8")

        signature_der = self._private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))

        # Convert DER signature to IEEE P1363 (64 bytes: r and s)
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        r, s = decode_dss_signature(signature_der)
        signature_raw = r.to_bytes(32, byteorder="big") + s.to_bytes(32, byteorder="big")
        sig_b64 = urlsafe_b64encode(signature_raw)

        jwt = f"{header_b64}.{claims_b64}.{sig_b64}"

        return {
            "Authorization": f"vapid t={jwt}, k={self.public_key_b64}",
            "Crypto-Key": f"p256ecdsa={self.public_key_b64}",
        }
