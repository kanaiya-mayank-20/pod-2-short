"""Signed cursors for DynamoDB pagination.

DynamoDB has no page numbers: each page returns a ``LastEvaluatedKey`` which the
client must send back as ``ExclusiveStartKey``. We hide that key in an opaque,
HMAC-signed string so clients cannot tamper with it and leak other partitions.
"""

import base64
import hashlib
import hmac
import json
from typing import Any

from core.config import settings
from core.exceptions import BadRequestError

SIGNATURE_LENGTH = 32
CURSOR_VERSION = b"v1|"


def _sign(raw: bytes) -> bytes:
    key = settings.SECRET_KEY.get_secret_value().encode()
    return hmac.new(key, raw, hashlib.sha256).digest()


def encode_cursor(last_evaluated_key: dict[str, Any]) -> str:
    payload = json.dumps(last_evaluated_key, separators=(",", ":"), sort_keys=True)
    raw = CURSOR_VERSION + payload.encode()
    return base64.urlsafe_b64encode(_sign(raw) + raw).decode()


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Return the ``LastEvaluatedKey`` a cursor stands for, or raise BadRequestError."""
    try:
        data = base64.urlsafe_b64decode(cursor.encode())
        if len(data) <= SIGNATURE_LENGTH:
            raise ValueError("cursor is too short")
        signature, raw = data[:SIGNATURE_LENGTH], data[SIGNATURE_LENGTH:]
        if not hmac.compare_digest(signature, _sign(raw)):
            raise ValueError("signature mismatch")
        decoded = json.loads(raw.removeprefix(CURSOR_VERSION))
        if not isinstance(decoded, dict):
            raise ValueError("cursor payload must be an object")
        return decoded
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BadRequestError("Invalid cursor") from exc
