import base64
import hashlib
import hmac
import json
import time
from typing import Any

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from pydantic import ValidationError

from app.integrations.feishu.schemas import FeishuEncryptedEnvelope


class FeishuCallbackSecurityError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def verify_callback_signature(
    *,
    raw_body: bytes,
    timestamp: str | None,
    nonce: str | None,
    signature: str | None,
    encrypt_key: str,
) -> None:
    if not timestamp or not nonce or not signature:
        raise FeishuCallbackSecurityError("missing_signature_headers")
    digest = hashlib.sha256()
    digest.update(timestamp.encode("utf-8"))
    digest.update(nonce.encode("utf-8"))
    digest.update(encrypt_key.encode("utf-8"))
    digest.update(raw_body)
    if not hmac.compare_digest(digest.hexdigest(), signature.lower()):
        raise FeishuCallbackSecurityError("invalid_signature")


def validate_callback_timestamp(
    value: str,
    *,
    max_age_seconds: int,
    now: int | None = None,
) -> int:
    if not value.isascii() or not value.isdigit():
        raise FeishuCallbackSecurityError("invalid_timestamp")
    raw_timestamp = int(value)
    if raw_timestamp >= 10**18:
        request_timestamp = raw_timestamp // 1_000_000_000
    elif raw_timestamp >= 10**15:
        request_timestamp = raw_timestamp // 1_000_000
    elif raw_timestamp >= 10**12:
        request_timestamp = raw_timestamp // 1_000
    else:
        request_timestamp = raw_timestamp
    current = int(time.time()) if now is None else now
    if abs(current - request_timestamp) > max_age_seconds:
        raise FeishuCallbackSecurityError("stale_callback")
    return request_timestamp


def decrypt_callback(raw_body: bytes, *, encrypt_key: str) -> dict[str, Any]:
    try:
        outer = FeishuEncryptedEnvelope.model_validate_json(raw_body)
        encrypted = base64.b64decode(outer.encrypt, validate=True)
        if len(encrypted) <= AES.block_size or len(encrypted) % AES.block_size:
            raise ValueError("invalid encrypted block length")
        key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
        plaintext = unpad(
            AES.new(key, AES.MODE_CBC, iv=encrypted[: AES.block_size]).decrypt(
                encrypted[AES.block_size :]
            ),
            AES.block_size,
        )
        payload = json.loads(plaintext.decode("utf-8"))
    except (ValidationError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FeishuCallbackSecurityError("invalid_encrypted_payload") from exc
    if not isinstance(payload, dict):
        raise FeishuCallbackSecurityError("invalid_encrypted_payload")
    return payload
