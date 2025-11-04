"""Helpers for generating Upbit-compatible JWT tokens."""

from __future__ import annotations

import base64
import hmac
import json
import uuid
from hashlib import sha256


def _b64url_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("utf-8")


def generate_jwt(access_key: str, secret_key: str, payload: dict[str, str] | None = None) -> str:
    """Create a signed JWT token for the Upbit API."""

    base_header = {"alg": "HS256", "typ": "JWT"}
    base_payload = {"access_key": access_key, "nonce": str(uuid.uuid4())}
    if payload:
        base_payload.update(payload)

    header_enc = _b64url_encode(json.dumps(base_header, separators=(",", ":")).encode())
    payload_enc = _b64url_encode(json.dumps(base_payload, separators=(",", ":")).encode())
    message = f"{header_enc}.{payload_enc}".encode()
    signature = hmac.new(secret_key.encode(), message, sha256).digest()
    return f"{header_enc}.{payload_enc}.{_b64url_encode(signature)}"


__all__ = ["generate_jwt"]
