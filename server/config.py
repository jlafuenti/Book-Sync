"""
BookSync Server Configuration

Loads settings from environment variables with sensible defaults.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


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

    # Server
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # External APIs
    google_books_api_key: Optional[str] = Field(default=None, alias="GOOGLE_BOOKS_API_KEY")

    # Audiobookshelf integration
    abs_url: Optional[str] = Field(default=None, alias="ABS_URL")
    abs_api_token: Optional[str] = Field(default=None, alias="ABS_API_TOKEN")
    abs_audiobooks_prefix: Optional[str] = Field(default=None, alias="ABS_AUDIOBOOKS_PREFIX")

    # Sync Settings
    default_rewind_seconds: int = 10

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
