"""
Centralized application configuration.

Every environment variable the app needs is declared here, ONCE, with a type.
If a required variable is missing, the app will fail to start immediately with
a clear error instead of failing later (e.g., mid-webhook) in a confusing way.

Why pydantic-settings: it validates types (so "20000" doesn't silently become
a string where you expected an int), gives you autocomplete in your editor,
and gives one obvious place to look when you ask "what env vars does this
app use?"
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App ----------------------------------------------------------------
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # ---- Interakt -------------------------------------------------------------
    interakt_api_key: str = Field(alias="INTERAKT_API_KEY")
    interakt_webhook_secret: str = Field(alias="INTERAKT_WEBHOOK_SECRET")
    interakt_base_url: str = "https://api.interakt.ai/v1/public"

    # ---- Session message text (no templates needed) --------------------------
    # These are the plain-text replies sent as session messages within the
    # 24-hour window. Since the student always messages first, we are always
    # inside that window. No WhatsApp template approval required.
    not_registered_message: str = Field(
        default=(
            "You are not registered for this course. "
            "If you believe this is a mistake, please contact our support team."
        ),
        alias="NOT_REGISTERED_MESSAGE",
    )
    not_completed_message: str = Field(
        default=(
            "Please complete the course before requesting your certificate. "
            "Once you finish all the required modules, just message "
            '"Certificate" again.'
        ),
        alias="NOT_COMPLETED_MESSAGE",
    )
    certificate_message: str = Field(
        default=(
            "Hi {name}, congratulations on completing the course! 🎓 "
            "Your certificate is attached. You can save or share it anytime."
        ),
        alias="CERTIFICATE_MESSAGE",
    )

    # ---- Google Sheets ----------------------------------------------------
    google_service_account_file: str = Field(
        default="secrets/service-account.json", alias="GOOGLE_SERVICE_ACCOUNT_FILE"
    )
    google_sheet_id: str = Field(alias="GOOGLE_SHEET_ID")
    google_sheet_worksheet_name: str = Field(
        default="Students", alias="GOOGLE_SHEET_WORKSHEET_NAME"
    )

    # ---- Google Drive (certificate storage) ----------------------------------
    # Optional: if set, certificates are uploaded into this specific folder.
    # If blank, the service account will create a "Certificates" folder in its
    # own Drive root automatically (no manual setup needed).
    google_drive_folder_id: str = Field(default="", alias="GOOGLE_DRIVE_FOLDER_ID")

    # ---- Certificate generation --------------------------------------------
    certificate_template_path: str = Field(alias="CERTIFICATE_TEMPLATE_PATH")
    certificate_font_path: str = Field(alias="CERTIFICATE_FONT_PATH")
    certificate_font_name: str = Field(
        default="CertFont", alias="CERTIFICATE_FONT_NAME"
    )
    certificate_name_x: float = Field(default=300, alias="CERTIFICATE_NAME_X")
    certificate_name_y: float = Field(default=350, alias="CERTIFICATE_NAME_Y")
    certificate_name_font_size: float = Field(
        default=32, alias="CERTIFICATE_NAME_FONT_SIZE"
    )

    # ---- Security -----------------------------------------------------------
    internal_admin_token: str = Field(alias="INTERNAL_ADMIN_TOKEN")

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    """
    Cached so the .env file / environment is only parsed once per process,
    not on every request. FastAPI's Depends(get_settings) will reuse this.
    """
    return Settings()
