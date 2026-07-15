import hashlib
import hmac

from app.utils.security import verify_interakt_signature

SECRET = "test-secret"


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_valid_signature_passes():
    body = b'{"hello": "world"}'
    signature = _sign(body, SECRET)
    assert verify_interakt_signature(body, signature, SECRET) is True


def test_invalid_signature_fails():
    body = b'{"hello": "world"}'
    bad_signature = _sign(body, "wrong-secret")
    assert verify_interakt_signature(body, bad_signature, SECRET) is False


def test_missing_signature_fails():
    body = b'{"hello": "world"}'
    assert verify_interakt_signature(body, None, SECRET) is False


def test_malformed_signature_header_fails():
    body = b'{"hello": "world"}'
    assert verify_interakt_signature(body, "not-a-real-signature", SECRET) is False


def test_tampered_body_fails():
    body = b'{"hello": "world"}'
    signature = _sign(body, SECRET)
    tampered_body = b'{"hello": "world!"}'
    assert verify_interakt_signature(tampered_body, signature, SECRET) is False
