"""
Tandem Server Configuration

Loads settings from environment variables with sensible defaults.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List, Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://booksync:booksync@localhost:5432/booksync",
        alias="DATABASE_URL",
    )

    # JWT Authentication
    jwt_secret_key: str = Field(
        default="dev-secret-change-me",
        alias="JWT_SECRET_KEY",
    )
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60 * 24  # 24 hours
    jwt_refresh_token_expire_days: int = 30
    # Short-lived, resource-scoped token for cover/audio URLs that can't carry
    # an Authorization header (img tags, Cast SDK media URLs). See issue #50.
    jwt_media_token_expire_minutes: int = 15

    # Deployment mode. "prod" (the default) refuses to start with known-default
    # secrets (JWT_SECRET_KEY / CREDENTIAL_ENC_KEYS); set to "dev" for local
    # development so zero-config defaults work.
    app_env: str = Field(default="prod", alias="APP_ENV")

    # Public self-registration (POST /api/auth/register). New accounts still
    # require admin approval (is_active=False) unless disabled here entirely.
    allow_public_registration: bool = Field(default=True, alias="ALLOW_PUBLIC_REGISTRATION")

    # Whisper Transcription
    whisper_model: str = Field(default="medium", alias="WHISPER_MODEL")
    whisper_device: str = Field(
        default="auto",
        alias="WHISPER_DEVICE",
        description="'auto' detects GPU, 'cpu' forces CPU, 'cuda' forces GPU",
    )
    
    # Remote Transcription
    transcription_provider: str = Field(
        default="remote_with_fallback", 
        alias="TRANSCRIPTION_PROVIDER",
        description="Options: 'local', 'remote', 'remote_with_fallback'"
    )
    transcription_remote_url: str = Field(default="", alias="TRANSCRIPTION_REMOTE_URL")
    transcription_remote_timeout: int = Field(default=7200, alias="TRANSCRIPTION_REMOTE_TIMEOUT")

    # Auto-transcribe
    auto_transcribe_enabled: bool = Field(default=False, alias="AUTO_TRANSCRIBE_ENABLED")

    # File Paths
    ebook_dir: str = Field(default="/data/ebooks", alias="EBOOK_DIR")
    audiobook_dir: str = Field(default="/data/audiobooks", alias="AUDIOBOOK_DIR")
    app_data_dir: str = Field(default="/data/app", alias="APP_DATA_DIR")
    covers_dir: str = Field(default="/data/app/covers", alias="COVERS_DIR")
    imports_dir: str = Field(default="/data/imports", alias="IMPORTS_DIR")
    # Directory where the server writes DB dumps + covers snapshots (nightly and
    # manual). Mounted read-write into the server. See docs/backup-restore.md.
    backups_dir: str = Field(default="/backups", alias="BACKUPS_DIR")

    # Upload guard (issue #157) — multipart/form-data requests whose declared
    # Content-Length exceeds this are refused with 413 BEFORE the multipart
    # parser runs (see middleware.py). Uploads are multi-GB audiobooks, so the
    # default is generous: 10 GiB.
    upload_max_bytes: int = Field(default=10 * 1024 * 1024 * 1024, alias="UPLOAD_MAX_BYTES")

    # Credential encryption — comma-separated list of Fernet keys.
    # First key is used to encrypt new writes; all keys are tried for decryption,
    # so rotation is "prepend a new key" with no migration step.
    credential_enc_keys: str = Field(default="", alias="CREDENTIAL_ENC_KEYS")

    # Server
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # CORS — comma-separated list of allowed origins; defaults to wildcard for dev
    cors_origins: str = Field(default="*", alias="CORS_ORIGINS")

    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS_ORIGINS into a list. '*' means allow all."""
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # External APIs
    google_books_api_key: Optional[str] = Field(default=None, alias="GOOGLE_BOOKS_API_KEY")

    # Audiobookshelf integration
    abs_url: Optional[str] = Field(default=None, alias="ABS_URL")
    abs_api_token: Optional[str] = Field(default=None, alias="ABS_API_TOKEN")
    abs_audiobooks_prefix: Optional[str] = Field(default=None, alias="ABS_AUDIOBOOKS_PREFIX")

    # Sync Settings
    # How far back a text→audio handoff lands from the matched sentence. Must
    # stay equal to the clients' resume-rewind offset (Android
    # PlaybackOffsets.RESUME_REWIND_MS, web RESUME_REWIND_SECONDS) — see
    # docs/position-sync-contract.md § Playback offsets (issue #42).
    #
    # NB: the only consumer, services.sync_engine.epub_to_audio, is currently
    # unreachable from any router — clients do the conversion locally off their
    # own sync-point cache. It is kept in step so the two can't disagree if a
    # server-side handoff is ever wired up.
    default_rewind_seconds: int = 5

    # Auto-completion thresholds — docs/position-sync-contract.md § Completion
    # (issue #56). A position write that *crosses into* the end zone marks the
    # book finished; explicit `is_completed` values always win and nothing is
    # ever auto-cleared. EPUB back-matter (acknowledgements, previews) means
    # 100 % is rarely reached while reading, hence 98.
    auto_complete_epub_percent: float = 98.0
    auto_complete_audio_tail_seconds: int = 120

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

# Known placeholder JWT secrets shipped in code/compose defaults — never valid
# for a production deployment.
DEFAULT_JWT_SECRETS = {"dev-secret-change-me", "change-me-to-a-random-secret-key"}


def check_jwt_secret(s: "Settings") -> None:
    """Refuse to run with a known-default JWT secret outside dev mode.

    Extracted as a plain function (rather than inlined in main.py's lifespan)
    so it's testable without booting the app.
    """
    import logging

    logger = logging.getLogger(__name__)
    if s.jwt_secret_key not in DEFAULT_JWT_SECRETS:
        return
    if s.app_env == "prod":
        raise RuntimeError(
            "JWT_SECRET_KEY is unset/default — refusing to start in prod. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\" "
            "and set JWT_SECRET_KEY, or set APP_ENV=dev for local development."
        )
    logger.warning(
        "Using default JWT secret key (dev mode). Set JWT_SECRET_KEY for production!"
    )


# Known placeholder Postgres credentials shipped in code/compose defaults —
# never valid for a production deployment.
DEFAULT_DB_CREDENTIALS = "booksync:booksync"


def check_db_credentials(s: "Settings") -> None:
    """Refuse to run with the default Postgres credentials outside dev mode."""
    import logging

    logger = logging.getLogger(__name__)
    if DEFAULT_DB_CREDENTIALS not in s.database_url:
        return
    if s.app_env == "prod":
        raise RuntimeError(
            "DATABASE_URL still contains the default booksync:booksync credentials — "
            "refusing to start in prod. Generate a real password with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(32))\" "
            "and set POSTGRES_PASSWORD/DATABASE_URL, or set APP_ENV=dev for local development."
        )
    logger.warning(
        "Using default Postgres credentials (dev mode). Set POSTGRES_PASSWORD for production!"
    )


def check_cors_origins(s: "Settings") -> None:
    """Refuse to run with a wildcard CORS origin outside dev mode."""
    import logging

    logger = logging.getLogger(__name__)
    if s.cors_origins_list != ["*"]:
        return
    if s.app_env == "prod":
        raise RuntimeError(
            "CORS_ORIGINS is unset/wildcard (\"*\") — refusing to start in prod. "
            "Set CORS_ORIGINS to a comma-separated list of allowed web origins "
            "(e.g. http://your-host:3000), or set APP_ENV=dev for local development."
        )
    logger.warning(
        "Using wildcard CORS origin (dev mode). Set CORS_ORIGINS for production!"
    )
