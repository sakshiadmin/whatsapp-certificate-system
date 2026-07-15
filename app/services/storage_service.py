"""
Google Drive upload — certificate PDF storage.

Replaces Cloudflare R2 with Google Drive. This reuses the SAME service account
already configured for Google Sheets (secrets/service-account.json), so there
is zero additional credential setup.

How it works:
  1. Upload the certificate PDF to a folder in the service account's Drive.
  2. Set the file's permission to "anyone with the link can view" — this makes
     the file publicly downloadable (same security model as R2: public URL,
     unguessable file ID).
  3. Return the direct download URL for WhatsApp/Interakt to fetch.

Why Google Drive instead of R2:
  - Free: 15 GB per Google account, certificates are ~100–200 KB each, so
    you can store ~75,000–150,000 certificates before hitting limits.
  - No extra credentials: reuses the service account already set up for Sheets.
  - No extra infrastructure: no S3 client, no bucket config, no public access
    domain setup.

Folder management:
  - If GOOGLE_DRIVE_FOLDER_ID is set in .env, files go into that folder.
  - Otherwise, the service creates a "Certificates" folder in the service
    account's own Drive on first upload and reuses it thereafter.
"""

from __future__ import annotations

import asyncio

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

from app.config import Settings
from app.logging_config import get_logger

logger = get_logger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
]


class StorageService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._service = None  # lazy-loaded on first upload
        self._folder_id: str | None = settings.google_drive_folder_id or None

    def _get_service(self):
        """Lazy-load the Drive service on first use (not at init) so that
        tests can swap in a FakeStorageService without needing a real
        service-account JSON file on disk."""
        if self._service is None:
            creds = Credentials.from_service_account_file(
                self._settings.google_service_account_file, scopes=SCOPES
            )
            self._service = build(
                "drive", "v3", credentials=creds, cache_discovery=False
            )
        return self._service

    def _ensure_folder(self) -> str:
        """Get or create the 'Certificates' folder. Called once, then cached."""
        if self._folder_id:
            return self._folder_id

        # Search for an existing folder named "Certificates" owned by us.
        query = (
            "mimeType = 'application/vnd.google-apps.folder' "
            "and name = 'Certificates' "
            "and trashed = false"
        )
        results = (
            self._get_service().files()
            .list(q=query, spaces="drive", fields="files(id, name)", pageSize=1)
            .execute()
        )
        files = results.get("files", [])
        if files:
            self._folder_id = files[0]["id"]
            logger.info("drive_folder_found", folder_id=self._folder_id)
            return self._folder_id

        # Create the folder.
        folder_metadata = {
            "name": "Certificates",
            "mimeType": "application/vnd.google-apps.folder",
        }
        folder = (
            self._get_service().files()
            .create(body=folder_metadata, fields="id")
            .execute()
        )
        self._folder_id = folder["id"]
        logger.info("drive_folder_created", folder_id=self._folder_id)
        return self._folder_id

    async def upload_certificate(self, certificate_id: str, pdf_bytes: bytes) -> str:
        """Upload a certificate PDF and return its public download URL."""

        def _upload() -> str:
            folder_id = self._ensure_folder()

            file_metadata = {
                "name": f"{certificate_id}.pdf",
                "parents": [folder_id],
            }
            media = MediaInMemoryUpload(
                pdf_bytes, mimetype="application/pdf", resumable=False
            )
            uploaded = (
                self._get_service().files()
                .create(body=file_metadata, media_body=media, fields="id")
                .execute()
            )
            file_id = uploaded["id"]

            # Make the file publicly readable (anyone with the link).
            self._get_service().permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                fields="id",
            ).execute()

            # Direct download URL that WhatsApp can fetch.
            public_url = f"https://drive.google.com/uc?id={file_id}&export=download"
            return public_url

        loop = asyncio.get_running_loop()
        public_url = await loop.run_in_executor(None, _upload)
        logger.info(
            "certificate_uploaded", certificate_id=certificate_id, url=public_url
        )
        return public_url
