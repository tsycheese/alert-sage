import base64
import hashlib
import json

import pytest
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from pydantic import ValidationError

from app.integrations.feishu.schemas import FeishuActionCommand, FeishuActionValue
from app.integrations.feishu.security import (
    FeishuCallbackSecurityError,
    decrypt_callback,
    validate_callback_timestamp,
    verify_callback_signature,
)

ENCRYPT_KEY = "synthetic-encrypt-key"
TIMESTAMP = "1786240000"
NONCE = "synthetic-nonce"


def encrypted_body(payload: dict[str, object]) -> bytes:
    key = hashlib.sha256(ENCRYPT_KEY.encode()).digest()
    iv = bytes(range(16))
    plaintext = json.dumps(payload, separators=(",", ":")).encode()
    ciphertext = AES.new(key, AES.MODE_CBC, iv=iv).encrypt(pad(plaintext, AES.block_size))
    return json.dumps(
        {"encrypt": base64.b64encode(iv + ciphertext).decode()},
        separators=(",", ":"),
    ).encode()


def signature(body: bytes, timestamp: str = TIMESTAMP) -> str:
    signed = timestamp.encode() + NONCE.encode() + ENCRYPT_KEY.encode() + body
    return hashlib.sha256(signed).hexdigest()


def test_signature_is_verified_before_aes_decryption() -> None:
    body = encrypted_body({"type": "url_verification", "token": "token", "challenge": "ok"})
    verify_callback_signature(
        raw_body=body,
        timestamp=TIMESTAMP,
        nonce=NONCE,
        signature=signature(body),
        encrypt_key=ENCRYPT_KEY,
    )
    assert decrypt_callback(body, encrypt_key=ENCRYPT_KEY)["challenge"] == "ok"

    opaque_timestamp = "2026-08-11T07:23:34Z"
    verify_callback_signature(
        raw_body=body,
        timestamp=opaque_timestamp,
        nonce=NONCE,
        signature=signature(body, opaque_timestamp),
        encrypt_key=ENCRYPT_KEY,
    )

    with pytest.raises(FeishuCallbackSecurityError, match="invalid_signature"):
        verify_callback_signature(
            raw_body=body,
            timestamp=TIMESTAMP,
            nonce=NONCE,
            signature="0" * 64,
            encrypt_key=ENCRYPT_KEY,
        )


def test_callback_timestamp_and_encrypted_payload_are_bounded() -> None:
    current = int(TIMESTAMP)
    assert (
        validate_callback_timestamp(str(current * 1_000), max_age_seconds=300, now=current)
        == current
    )
    assert (
        validate_callback_timestamp(str(current * 1_000_000), max_age_seconds=300, now=current)
        == current
    )
    assert (
        validate_callback_timestamp(str(current * 1_000_000_000), max_age_seconds=300, now=current)
        == current
    )
    with pytest.raises(FeishuCallbackSecurityError, match="stale_callback"):
        validate_callback_timestamp(
            TIMESTAMP,
            max_age_seconds=300,
            now=current + 301,
        )
    with pytest.raises(FeishuCallbackSecurityError, match="invalid_timestamp"):
        validate_callback_timestamp(
            "not-a-time",
            max_age_seconds=300,
            now=current,
        )
    with pytest.raises(FeishuCallbackSecurityError, match="invalid_encrypted_payload"):
        decrypt_callback(b'{"encrypt":"not-base64"}', encrypt_key=ENCRYPT_KEY)


def test_action_value_and_reanalysis_feedback_are_strict() -> None:
    value = FeishuActionValue(
        binding_id="00000000-0000-0000-0000-000000000001",
        action="reanalyze",
        revision=2,
        nonce="n" * 32,
    )
    with pytest.raises(ValidationError, match="feedback is required"):
        FeishuActionCommand.model_validate(
            {
                "callback": {
                    "schema": "2.0",
                    "header": {
                        "event_id": "event-1",
                        "event_type": "card.action.trigger",
                        "create_time": "1786240000000",
                        "token": "token",
                        "app_id": "cli_test",
                        "tenant_key": "tenant-test",
                    },
                    "event": {
                        "operator": {"open_id": "ou_test"},
                        "token": "synthetic-action-token",
                        "context": {"open_message_id": "om_test", "open_chat_id": "oc_test"},
                        "action": {"tag": "button", "value": value.model_dump(mode="json")},
                    },
                },
                "value": value.model_dump(mode="json"),
            }
        )
