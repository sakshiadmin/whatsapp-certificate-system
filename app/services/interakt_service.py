"""
Interakt API integration — sending WhatsApp messages via SESSION messages.

WHY SESSION MESSAGES INSTEAD OF TEMPLATES
-------------------------------------------
WhatsApp allows businesses to send free-form text and media messages
("session messages") within 24 hours of the customer's last message. Since
our flow is always: student sends "Certificate" → server replies within
seconds, we are ALWAYS inside the 24h session window.

Benefits:
  - ZERO COST: session messages have no per-message charge from Meta (unlike
    templates which cost ~₹0.15–0.50 each).
  - NO TEMPLATE APPROVAL: no waiting 24–48 hours for Meta to review templates,
    no risk of rejection, no template management overhead.
  - FLEXIBILITY: you can change the reply text any time without resubmitting
    for approval — just update the env var or config.

Interakt's API for session messages uses the same /message/ endpoint but with
type "Text" for plain text or type "Image"/"Document" for media, instead of
type "Template".

RETRY STRATEGY
----------------
Same as before: retry on network errors and 5xx responses up to 3 times
with exponential backoff. No retry on 4xx.
"""

from __future__ import annotations

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import Settings
from app.logging_config import get_logger

logger = get_logger(__name__)


class InteraktAPIError(Exception):
    def __init__(self, status_code: int, body: str):
        super().__init__(f"Interakt API error {status_code}: {body}")
        self.status_code = status_code
        self.body = body


class RetryableInteraktError(InteraktAPIError):
    """5xx / network-level errors — safe to retry."""


class InteraktService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.interakt_base_url,
            headers={
                "Authorization": f"Basic {settings.interakt_api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(15.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _split_phone(full_phone_digits: str) -> tuple[str, str]:
        """
        Interakt's API wants countryCode ("+91") and phoneNumber (national
        number, no country code) as SEPARATE fields.
        We only reliably know the full digit string from the webhook
        (e.g. "919876543210"), so we assume Indian numbers (+91, 10-digit
        subscriber number) by default. If you serve students outside
        India, replace this with a proper phone-number library
        (e.g. `phonenumbers`) that parses country code correctly.
        """
        if full_phone_digits.startswith("91") and len(full_phone_digits) == 12:
            return "+91", full_phone_digits[2:]
        # Fallback: last 10 digits are the number, whatever remains is the
        # country code. Good enough for a single-country rollout; replace
        # for multi-country support.
        return "+91", full_phone_digits[-10:]

    @retry(
        retry=retry_if_exception_type(RetryableInteraktError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def _post_message(self, payload: dict) -> dict:
        response = await self._client.post("/message/", json=payload)
        if response.status_code >= 500:
            raise RetryableInteraktError(response.status_code, response.text)
        if response.status_code >= 400:
            raise InteraktAPIError(response.status_code, response.text)
        return response.json()

    async def send_certificate(
        self,
        full_phone_digits: str,
        student_name: str,
        certificate_url: str,
        certificate_id: str,
    ) -> dict:
        """Send the certificate PDF as a session document message."""
        country_code, phone_number = self._split_phone(full_phone_digits)

        # First, send the document (certificate PDF).
        doc_payload = {
            "countryCode": country_code,
            "phoneNumber": phone_number,
            "callbackData": certificate_id,
            "type": "Document",
            "data": {
                "mediaUrl": certificate_url,
                "message": self._settings.certificate_message.format(
                    name=student_name
                ),
                "filename": f"{certificate_id}.pdf",
            },
        }
        result = await self._post_message(doc_payload)
        logger.info(
            "certificate_message_sent",
            phone=full_phone_digits,
            certificate_id=certificate_id,
            interakt_message_id=result.get("id"),
        )
        return result

    async def send_not_registered_reply(self, full_phone_digits: str) -> dict:
        """Send a plain session text message for unregistered students."""
        country_code, phone_number = self._split_phone(full_phone_digits)
        payload = {
            "countryCode": country_code,
            "phoneNumber": phone_number,
            "type": "Text",
            "data": {
                "message": self._settings.not_registered_message,
            },
        }
        return await self._post_message(payload)

    async def send_not_completed_reply(self, full_phone_digits: str) -> dict:
        """Send a plain session text message for students who haven't completed."""
        country_code, phone_number = self._split_phone(full_phone_digits)
        payload = {
            "countryCode": country_code,
            "phoneNumber": phone_number,
            "type": "Text",
            "data": {
                "message": self._settings.not_completed_message,
            },
        }
        return await self._post_message(payload)
