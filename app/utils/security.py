"""
Webhook signature verification.

Interakt signs every webhook request with an HMAC-SHA256 signature in the
"Interakt-Signature" header, computed over the raw request body using the
secret key you set in Interakt's Developer Settings. The header value looks
like: "sha256=<hex digest>".

Without this check, ANYONE who discovers your webhook URL could POST a fake
payload claiming "student X completed the course" and get a certificate
issued, or spam your server. This check is not optional for a production
system.

We compare using hmac.compare_digest, which runs in constant time — a naive
`==` string comparison leaks timing information that can (in theory) be used
to guess the correct signature byte-by-byte.
"""

import hashlib
import hmac


def verify_interakt_signature(raw_body: bytes, signature_header: str | None, secret: str) -> bool:
    if not signature_header:
        return False

    if not signature_header.startswith("sha256="):
        return False

    provided_signature = signature_header.removeprefix("sha256=").strip().lower()

    expected_signature = hmac.new(
        key=secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(provided_signature, expected_signature)
