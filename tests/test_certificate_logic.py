from app.main import TRIGGER_PATTERN
from app.services.certificate_service import generate_unique_certificate_id


def test_certificate_id_format():
    cert_id = generate_unique_certificate_id(existing_ids=set())
    assert cert_id.startswith("CERT-")
    parts = cert_id.split("-")
    assert len(parts) == 3
    assert len(parts[2]) == 6


def test_certificate_id_avoids_collisions():
    existing = {generate_unique_certificate_id(set()) for _ in range(50)}
    new_id = generate_unique_certificate_id(existing)
    assert new_id not in existing


def test_trigger_matches_plain_word():
    assert TRIGGER_PATTERN.match("Certificate")
    assert TRIGGER_PATTERN.match("certificate")
    assert TRIGGER_PATTERN.match("  CERTIFICATE  ")
    assert TRIGGER_PATTERN.match("certificate!")


def test_trigger_does_not_match_sentences():
    assert not TRIGGER_PATTERN.match("can I get my certificate please")
    assert not TRIGGER_PATTERN.match("hi")
    assert not TRIGGER_PATTERN.match("")
