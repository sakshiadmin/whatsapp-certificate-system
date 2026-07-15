"""
Google Sheets integration.

Sheet layout (row 1 is the header row, exactly these column names):

    Phone | Name | Status | Certificate Sent | Certificate ID | Certificate URL | Timestamp

Design notes for ~20,000 students
----------------------------------
Naively calling the Sheets API on every single incoming WhatsApp message
(e.g. "find the row where Phone == X") is slow (network round-trip to
Google on every message) and burns your API quota (Google's default quota
is 300 read requests per minute per project — fine for normal traffic, but
you don't want every message costing a request if you can avoid it).

Instead we:
1. Read the ENTIRE sheet in one API call (get_all_values) and build an
   in-memory dict {normalized_phone: StudentRecord}. One read handles all
   20,000 students.
2. Cache that dict in memory for CACHE_TTL_SECONDS. Most "Certificate"
   requests will hit students who registered a while ago — the sheet
   doesn't change every second, so a short cache is safe and dramatically
   cuts down on API calls when many students message around the same time
   (e.g., right after a course deadline).
3. On a cache miss (phone not found) we force ONE refresh before giving up,
   in case the student was added to the sheet moments ago and the cache is
   stale. This avoids a false "not registered" reply.
4. Writes (marking a certificate as sent) go directly to the Sheet via a
   targeted `update_cells` call that only touches the changed cells,
   not the whole row — this is both faster and safer (you never
   accidentally clobber a column you didn't mean to touch).

Phone number normalization
---------------------------
WhatsApp/Interakt sends phone numbers as digits only, with country code,
no "+" (e.g. "919876543210"). Make sure the "Phone" column in your Sheet is
stored the SAME way (as TEXT, not a Number — otherwise Sheets/Excel will
strip a leading "0" or convert it to scientific notation for long numbers).
We still normalize defensively in code (strip spaces, "+", leading zeros)
so small inconsistencies in the sheet don't cause false negatives.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

from app.config import Settings
from app.logging_config import get_logger
from app.models import StudentRecord

logger = get_logger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

REQUIRED_COLUMNS = [
    "Phone",
    "Name",
    "Status",
    "Certificate Sent",
    "Certificate ID",
    "Certificate URL",
    "Timestamp",
]

CACHE_TTL_SECONDS = 60


def normalize_phone(raw_phone: str) -> str:
    """Digits only, no '+', no leading zero, no spaces/dashes."""
    digits = "".join(ch for ch in raw_phone if ch.isdigit())
    return digits.lstrip("0") if len(digits) > 10 else digits


class SheetsService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: gspread.Client | None = None
        self._worksheet: gspread.Worksheet | None = None

        self._cache: dict[str, StudentRecord] = {}
        self._cache_loaded_at: float = 0.0
        self._cache_lock = asyncio.Lock()

    # -- connection setup ------------------------------------------------------
    def _connect(self) -> gspread.Worksheet:
        if self._worksheet is not None:
            return self._worksheet

        creds = Credentials.from_service_account_file(
            self._settings.google_service_account_file, scopes=SCOPES
        )
        self._client = gspread.authorize(creds)
        spreadsheet = self._client.open_by_key(self._settings.google_sheet_id)
        self._worksheet = spreadsheet.worksheet(
            self._settings.google_sheet_worksheet_name
        )
        return self._worksheet

    # -- cache -----------------------------------------------------------------
    async def _refresh_cache(self) -> None:
        """Blocking gspread calls are run in a thread so we don't block the
        FastAPI event loop (gspread has no native async client)."""

        def _load() -> dict[str, StudentRecord]:
            worksheet = self._connect()
            all_values = worksheet.get_all_values()  # 1 API call, whole sheet
            if not all_values:
                raise RuntimeError("Google Sheet is empty — no header row found.")

            header = all_values[0]
            missing = [c for c in REQUIRED_COLUMNS if c not in header]
            if missing:
                raise RuntimeError(
                    f"Google Sheet is missing required column(s): {missing}. "
                    f"Found columns: {header}"
                )
            col_index = {name: header.index(name) for name in REQUIRED_COLUMNS}

            records: dict[str, StudentRecord] = {}
            for row_number, row in enumerate(all_values[1:], start=2):
                # Skip fully blank rows (common at the bottom of a sheet).
                if not any(cell.strip() for cell in row):
                    continue

                def get(col: str) -> str:
                    idx = col_index[col]
                    return row[idx].strip() if idx < len(row) else ""

                phone_raw = get("Phone")
                if not phone_raw:
                    continue

                record = StudentRecord(
                    row_number=row_number,
                    phone=phone_raw,
                    name=get("Name"),
                    status=get("Status"),
                    certificate_sent=get("Certificate Sent"),
                    certificate_id=get("Certificate ID"),
                    certificate_url=get("Certificate URL"),
                    timestamp=get("Timestamp"),
                )
                records[normalize_phone(phone_raw)] = record
            return records

        loop = asyncio.get_running_loop()
        loaded = await loop.run_in_executor(None, _load)
        self._cache = loaded
        self._cache_loaded_at = time.monotonic()
        logger.info("sheets_cache_refreshed", student_count=len(loaded))

    async def _ensure_cache(self, force: bool = False) -> None:
        stale = (time.monotonic() - self._cache_loaded_at) > CACHE_TTL_SECONDS
        if force or stale or not self._cache:
            async with self._cache_lock:
                # Re-check inside the lock in case another request already
                # refreshed while we were waiting.
                stale = (time.monotonic() - self._cache_loaded_at) > CACHE_TTL_SECONDS
                if force or stale or not self._cache:
                    await self._refresh_cache()

    # -- public API --------------------------------------------------------
    async def find_student(self, phone_number: str) -> StudentRecord | None:
        normalized = normalize_phone(phone_number)

        await self._ensure_cache()
        record = self._cache.get(normalized)
        if record:
            return record

        # Cache miss: force ONE refresh in case the student is brand new,
        # then give up. Prevents false "not registered" replies for
        # students added seconds ago, without refreshing on every miss.
        await self._ensure_cache(force=True)
        return self._cache.get(normalized)

    async def mark_certificate_sent(
        self, record: StudentRecord, certificate_id: str, certificate_url: str
    ) -> None:
        """Writes 'Sent', the certificate ID, the Drive URL, and a UTC
        timestamp back to the student's row. Only touches those 4 cells."""

        def _write() -> None:
            worksheet = self._connect()
            header = worksheet.row_values(1)
            updates = []
            values = {
                "Certificate Sent": "Sent",
                "Certificate ID": certificate_id,
                "Certificate URL": certificate_url,
                "Timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            for col_name, value in values.items():
                col_idx = header.index(col_name) + 1  # gspread is 1-indexed
                updates.append(
                    gspread.cell.Cell(row=record.row_number, col=col_idx, value=value)
                )
            worksheet.update_cells(updates)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _write)

        # Keep the in-memory cache consistent immediately, rather than
        # waiting up to CACHE_TTL_SECONDS for the next scheduled refresh —
        # this is what prevents a duplicate send if the same student's
        # message is retried right after this write.
        normalized = normalize_phone(record.phone)
        cached = self._cache.get(normalized)
        if cached:
            cached.certificate_sent = "Sent"
            cached.certificate_id = certificate_id
            cached.certificate_url = certificate_url

        logger.info(
            "sheet_updated_certificate_sent",
            phone=record.phone,
            certificate_id=certificate_id,
            row=record.row_number,
        )
