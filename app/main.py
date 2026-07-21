"""
FastAPI application entry point.

Endpoints
---------
GET  /healthz              -> liveness/readiness check for the host platform
POST /webhooks/interakt     -> the ONE webhook URL you configure in Interakt
POST /admin/refresh-cache   -> force-refresh the Sheets cache (protected)

Everything else (Sheets, PDF generation, Google Drive upload, Interakt sending)
lives in app/services/*. This file's only job is: verify the request is real,
figure out what the student wants, and call the right services in order.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.logging_config import configure_logging, get_logger
from app.models import InteraktWebhookPayload
from app.services.certificate_service import (
    generate_certificate_pdf,
    generate_unique_certificate_id,
)
from app.services.interakt_service import InteraktAPIError, InteraktService
from app.services.sheets_service import SheetsService
from app.services.storage_service import StorageService
from app.utils.idempotency import phone_lock_manager
from app.utils.security import verify_interakt_signature

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)

# Trigger word matching: the spec says the student sends "Certificate".
# We match case-insensitively and ignore surrounding whitespace/punctuation
# so "certificate", "Certificate!", " CERTIFICATE " all trigger it, but we
# deliberately do NOT do fuzzy/substring matching (e.g. a message that just
# mentions the word "certificate" in a sentence) to avoid accidental
# triggers on unrelated chats.
TRIGGER_PATTERN = re.compile(r"^\s*certificate\s*[!.?]*\s*$", re.IGNORECASE)
START_TRIGGER_PATTERN = re.compile(r"^\s*let'?s\s+start\s*[!.?]*\s*$", re.IGNORECASE)

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.sheets_service = SheetsService(settings)
    app.state.interakt_service = InteraktService(settings)
    app.state.storage_service = StorageService(settings)
    logger.info("app_startup_complete", app_env=settings.app_env)
    yield
    await app.state.interakt_service.aclose()
    logger.info("app_shutdown_complete")


app = FastAPI(title="WhatsApp Certificate Generation System", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


def _extract_text_phone_and_name(payload: InteraktWebhookPayload) -> tuple[str | None, str | None, str]:
    """
    Defensive extraction of (message_text, phone_digits, name) from the webhook.

    Interakt's incoming-message webhook nests these under data.customer and
    data.message. This function is intentionally the ONLY place that reads
    that nested structure — if Interakt changes field names on your account,
    or you notice a slightly different shape in your logs (see README ->
    Testing Guide -> "Capture one real webhook first"), this is the one
    function to update.
    """
    if payload.data is None:
        return None, None, ""

    phone = None
    name = ""
    if payload.data.customer is not None:
        phone = payload.data.customer.channel_phone_number
        name = payload.data.customer.traits.get("name", "") if payload.data.customer.traits else ""

    text = None
    if payload.data.message is not None:
        text = payload.data.message.message

    return text, phone, name


@app.post("/webhooks/interakt")
async def interakt_webhook(
    request: Request,
    interakt_signature: str | None = Header(default=None, alias="Interakt-Signature"),
):
    raw_body = await request.body()

    if not verify_interakt_signature(
        raw_body, interakt_signature, settings.interakt_webhook_secret
    ):
        logger.warning("webhook_signature_invalid")
        # 401 tells Interakt "don't retry this, it's not going to work" —
        # appropriate here since a bad signature will never fix itself on
        # retry, unlike a transient 500.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        payload = InteraktWebhookPayload.model_validate_json(raw_body)
    except Exception:
        logger.warning("webhook_payload_unparseable", raw_body=raw_body[:2000])
        # Still 200 — malformed/unexpected payloads (e.g. a webhook type we
        # don't handle) should not cause Interakt to keep retrying forever.
        return JSONResponse(status_code=200, content={"status": "ignored"})

    message_text, phone, name = _extract_text_phone_and_name(payload)

    if not phone or not message_text:
        logger.info("webhook_ignored_no_text_or_phone", webhook_type=payload.type)
        return {"status": "ignored"}

    if START_TRIGGER_PATTERN.match(message_text):
        logger.info("start_request_received", phone=phone)
        await _handle_start_request(request.app, phone, name)
        return {"status": "processed"}

    if not TRIGGER_PATTERN.match(message_text):
        logger.info("webhook_ignored_not_trigger", phone=phone)
        return {"status": "ignored"}

    logger.info("certificate_request_received", phone=phone)
    await _handle_certificate_request(request.app, phone)
    return {"status": "processed"}


async def _handle_start_request(app: FastAPI, phone: str, name: str) -> None:
    sheets: SheetsService = app.state.sheets_service

    lock = phone_lock_manager.get_lock(phone)
    async with lock:
        student = await sheets.find_student(phone)
        if student is None:
            await sheets.add_student(phone, name, status="IN_PROGRESS")
            logger.info("student_started_and_added", phone=phone)
        else:
            await sheets.update_student_status(student, status="IN_PROGRESS")
            logger.info("student_status_updated_to_in_progress", phone=phone)


async def _handle_certificate_request(app: FastAPI, phone: str) -> None:
    sheets: SheetsService = app.state.sheets_service
    interakt: InteraktService = app.state.interakt_service
    storage: StorageService = app.state.storage_service

    student = await sheets.find_student(phone)

    if student is None:
        logger.info("student_not_registered", phone=phone)
        try:
            await interakt.send_not_registered_reply(phone)
        except InteraktAPIError:
            logger.exception("failed_to_send_not_registered_reply", phone=phone)
        return

    if not student.is_completed:
        logger.info("student_not_completed", phone=phone, status=student.status)
        try:
            await interakt.send_not_completed_reply(phone)
        except InteraktAPIError:
            logger.exception("failed_to_send_not_completed_reply", phone=phone)
        return

    # From here on we hold a per-phone-number lock: see
    # app/utils/idempotency.py for exactly what this does and does not
    # protect against.
    lock = phone_lock_manager.get_lock(phone)
    async with lock:
        # Re-fetch fresh state now that we hold the lock — another request
        # for this same phone number may have completed while we waited.
        student = await sheets.find_student(phone)
        if student is None or not student.is_completed:
            return  # state changed underneath us; nothing to do

        if student.certificate_already_sent and student.certificate_id:
            logger.info(
                "certificate_already_sent_resending_existing",
                phone=phone,
                certificate_id=student.certificate_id,
            )
            # Use the stored Google Drive URL from the sheet.
            # Unlike R2 (where URLs were deterministic from the cert ID),
            # Google Drive URLs contain a file ID that we store at first-send.
            certificate_url = student.certificate_url
            if not certificate_url:
                # Fallback: if no URL stored (e.g. migrated from old system),
                # regenerate and re-upload rather than failing silently.
                logger.warning(
                    "certificate_url_missing_regenerating",
                    phone=phone,
                    certificate_id=student.certificate_id,
                )
                # Let it fall through to the generation path below
            else:
                try:
                    await interakt.send_certificate(
                        phone, student.name, certificate_url, student.certificate_id
                    )
                except InteraktAPIError:
                    logger.exception("failed_to_resend_certificate", phone=phone)
                return

        existing_ids = {
            record.certificate_id
            for record in sheets._cache.values()  # noqa: SLF001 (internal, same module family)
            if record.certificate_id
        }
        certificate_id = generate_unique_certificate_id(existing_ids)

        try:
            pdf_bytes = generate_certificate_pdf(settings, student.name, certificate_id)
            certificate_url = await storage.upload_certificate(certificate_id, pdf_bytes)
            await interakt.send_certificate(
                phone, student.name, certificate_url, certificate_id
            )
        except Exception:
            logger.exception("certificate_generation_or_send_failed", phone=phone)
            # We deliberately do NOT mark the sheet as "Sent" if anything
            # above failed — this is what prevents a failed attempt from
            # permanently blocking a legitimate retry. The student can send
            # "Certificate" again and the whole flow will retry cleanly.
            return

        await sheets.mark_certificate_sent(student, certificate_id, certificate_url)
        logger.info("certificate_flow_complete", phone=phone, certificate_id=certificate_id)


@app.post("/admin/refresh-cache")
async def refresh_cache(
    request: Request,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    if x_admin_token != settings.internal_admin_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    sheets: SheetsService = request.app.state.sheets_service
    await sheets._ensure_cache(force=True)  # noqa: SLF001
    return {"status": "refreshed", "student_count": len(sheets._cache)}  # noqa: SLF001
