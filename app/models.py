"""
Data models.

Two categories:
1. Interakt webhook payload models — these describe the JSON Interakt sends
   US. We only model the fields we actually read; everything else is
   ignored (extra="ignore") because Interakt can add fields over time and we
   don't want that to break parsing.
2. Internal domain models — StudentRecord is our own clean representation
   of a row in the Google Sheet, independent of gspread's row/column
   plumbing.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# -----------------------------------------------------------------------------
# Interakt incoming-message webhook
# -----------------------------------------------------------------------------
# Real shape (relevant fields only), per Interakt's "incoming customer message"
# webhook:
# {
#   "version": "1.0",
#   "timestamp": "...",
#   "type": "message_received",
#   "data": {
#     "customer": {
#       "id": "...",
#       "channel_phone_number": "919999999999",
#       "traits": {"name": "..."}
#     },
#     "message": {
#       "id": "...",
#       "message_status": "...",
#       "type": "Text" | "Document" | ...,
#       "message": "Certificate"   <-- the text body the student typed
#     }
#   }
# }
#
# NOTE: Interakt's exact field names for the inbound message body have
# changed across their doc revisions. The webhook handler below is written
# defensively — see extract_text_and_phone() — so that if Interakt nests the
# text slightly differently on your account, one small function needs
# updating instead of the whole codebase. ALWAYS verify the exact shape by
# looking at a real payload in your logs before going live (see README ->
# Testing Guide -> "Capture one real webhook first").


class InteraktCustomer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: Optional[str] = None
    channel_phone_number: Optional[str] = None
    traits: dict[str, Any] = Field(default_factory=dict)


class InteraktMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: Optional[str] = None
    message: Optional[str] = None
    message_status: Optional[str] = None
    type: Optional[str] = None


class InteraktWebhookData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    customer: Optional[InteraktCustomer] = None
    message: Optional[InteraktMessage] = None


class InteraktWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: Optional[str] = None
    timestamp: Optional[str] = None
    type: Optional[str] = None
    data: Optional[InteraktWebhookData] = None


# -----------------------------------------------------------------------------
# Internal domain models
# -----------------------------------------------------------------------------
class StudentStatus(str, Enum):
    REGISTERED = "REGISTERED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class StudentRecord(BaseModel):
    """One row of the Google Sheet, normalized."""

    row_number: int  # 1-indexed sheet row, needed for in-place updates
    phone: str
    name: str
    status: str
    certificate_sent: str
    certificate_id: str
    certificate_url: str
    timestamp: str

    @property
    def is_completed(self) -> bool:
        return self.status.strip().upper() == StudentStatus.COMPLETED.value

    @property
    def certificate_already_sent(self) -> bool:
        return self.certificate_sent.strip().upper() == "SENT"
