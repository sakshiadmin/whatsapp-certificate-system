"""
Integration test for POST /webhooks/interakt using fake services.

This test does NOT hit Google Sheets, Interakt, or Google Drive — it swaps in
fake in-memory versions so you can run `pytest` with zero external accounts
configured. See README -> Testing Guide for how to test against the REAL
services once you have credentials.
"""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models import StudentRecord

SECRET = None  # filled in by the settings fixture below


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class FakeSheetsService:
    def __init__(self, students: dict[str, StudentRecord]):
        self._students = students
        self._cache = students
        self.marked_sent: list[str] = []

    async def find_student(self, phone_number: str):
        return self._students.get(phone_number)

    async def mark_certificate_sent(self, record, certificate_id, certificate_url):
        record.certificate_sent = "Sent"
        record.certificate_id = certificate_id
        record.certificate_url = certificate_url
        self.marked_sent.append(record.phone)


class FakeInteraktService:
    def __init__(self):
        self.sent_not_registered: list[str] = []
        self.sent_not_completed: list[str] = []
        self.sent_certificates: list[str] = []

    async def aclose(self) -> None:
        pass

    async def send_not_registered_reply(self, phone):
        self.sent_not_registered.append(phone)

    async def send_not_completed_reply(self, phone):
        self.sent_not_completed.append(phone)

    async def send_certificate(self, phone, name, url, cert_id):
        self.sent_certificates.append(phone)


class FakeStorageService:
    async def upload_certificate(self, certificate_id, pdf_bytes):
        return f"https://drive.google.com/uc?id=FAKE_FILE_ID&export=download"


@pytest.fixture
def client(monkeypatch, tmp_path):
    # Point certificate generation at a tiny real PDF + a real font so the
    # actual PDF-drawing code path is exercised, not just mocked away.
    from reportlab.pdfgen import canvas

    template_path = tmp_path / "template.pdf"
    c = canvas.Canvas(str(template_path), pagesize=(842, 595))
    c.drawString(100, 500, "CERTIFICATE OF COMPLETION")
    c.save()

    monkeypatch.setenv("CERTIFICATE_TEMPLATE_PATH", str(template_path))
    monkeypatch.setenv(
        "CERTIFICATE_FONT_PATH",
        "fonts/Allura-Regular.ttf",
    )
    monkeypatch.setenv("INTERAKT_API_KEY", "test")
    monkeypatch.setenv("INTERAKT_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "unused.json")
    monkeypatch.setenv("GOOGLE_SHEET_ID", "unused")
    monkeypatch.setenv("INTERNAL_ADMIN_TOKEN", "test-admin-token")
    get_settings.cache_clear()

    # Reset the font registration flag so the test font path is used.
    import app.services.certificate_service as cert_mod
    cert_mod._font_registered = False

    # Re-bind the module-level `settings` in main.py so it picks up our
    # monkeypatched env vars instead of whatever .env had at first import.
    import app.main as main_module
    main_module.settings = get_settings()

    with TestClient(app) as test_client:
        test_client.app.state.sheets_service = FakeSheetsService(
            {
                "919999999999": StudentRecord(
                    row_number=2,
                    phone="919999999999",
                    name="Asha Rao",
                    status="COMPLETED",
                    certificate_sent="",
                    certificate_id="",
                    certificate_url="",
                    timestamp="",
                ),
                "918888888888": StudentRecord(
                    row_number=3,
                    phone="918888888888",
                    name="Ravi Kumar",
                    status="IN_PROGRESS",
                    certificate_sent="",
                    certificate_id="",
                    certificate_url="",
                    timestamp="",
                ),
            }
        )
        test_client.app.state.interakt_service = FakeInteraktService()
        test_client.app.state.storage_service = FakeStorageService()
        yield test_client


def _post_webhook(client: TestClient, phone: str, text: str):
    body = {
        "version": "1.0",
        "timestamp": "2026-07-14T00:00:00Z",
        "type": "message_received",
        "data": {
            "customer": {"id": "abc", "channel_phone_number": phone, "traits": {}},
            "message": {"id": "msg1", "message": text, "type": "Text"},
        },
    }
    raw = json.dumps(body).encode()
    signature = _sign(raw, "test-secret")
    return client.post(
        "/webhooks/interakt",
        data=raw,
        headers={"Interakt-Signature": signature, "Content-Type": "application/json"},
    )


def test_completed_student_gets_certificate(client):
    response = _post_webhook(client, "919999999999", "Certificate")
    assert response.status_code == 200
    assert client.app.state.interakt_service.sent_certificates == ["919999999999"]
    assert client.app.state.sheets_service.marked_sent == ["919999999999"]


def test_incomplete_student_gets_not_completed_reply(client):
    response = _post_webhook(client, "918888888888", "Certificate")
    assert response.status_code == 200
    assert client.app.state.interakt_service.sent_not_completed == ["918888888888"]


def test_unknown_student_gets_not_registered_reply(client):
    response = _post_webhook(client, "917777777777", "Certificate")
    assert response.status_code == 200
    assert client.app.state.interakt_service.sent_not_registered == ["917777777777"]


def test_invalid_signature_rejected(client):
    body = json.dumps({"type": "message_received"}).encode()
    response = client.post(
        "/webhooks/interakt",
        data=body,
        headers={"Interakt-Signature": "sha256=deadbeef"},
    )
    assert response.status_code == 401


def test_non_trigger_message_ignored(client):
    response = _post_webhook(client, "919999999999", "hello there")
    assert response.status_code == 200
    assert client.app.state.interakt_service.sent_certificates == []
