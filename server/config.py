"""
Tandem Server Configuration

Loads settings from environment variables with sensible defaults.
"""

import os
from urllib.parse import quote

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from pydantic import Field, model_validator
from typing import Any, List, Optional


# ── Secrets from files, not from the environment (issue #180) ────────────────
#
# A secret in compose `environment:` is printed by `docker inspect`, sits in
# `/proc/1/environ`, and is readable by anyone in the `docker` group or with
# code execution in the container. The Docker/Swarm convention is to put the
# value in a file and pass only its path:
#
#   JWT_SECRET_KEY_FILE=/run/secrets/jwt_secret_key
#
# Every name below accepts that `<NAME>_FILE` form, which takes precedence over
# the plain `<NAME>`. A `<NAME>_FILE` that cannot be turned into a secret is a
# startup error naming the variable — never a silent fall back to the plain
# variable, because "silently kept running on the shipped default secret" is
# the exact failure this mechanism exists to prevent.
#
# Adding a secret setting? Add its alias here too; a test in
# server/tests/test_secret_files.py fails on any `..._KEY`/`..._TOKEN`/
# `..._PASSWORD`/`..._SECRET` alias that is missing from this tuple.
SECRET_FILE_ENV_VARS = (
    "DATABASE_URL",
    "JWT_SECRET_KEY",
    "CREDENTIAL_ENC_KEYS",
    "POSTGRES_PASSWORD",
    "GOOGLE_BOOKS_API_KEY",
    "ABS_API_TOKEN",
)

SECRET_FILE_SUFFIX = "_FILE"


def read_secret_file(var_name: str, path: str) -> str:
    """The secret held in `path`, or a RuntimeError naming `var_name`.

    One trailing newline (or CRLF) is stripped: every way of writing a secret
    to a file adds one, and a JWT signing key with a `\\n` on the end is a
    silent mismatch against the same key entered anywhere else. Nothing else is
    touched — leading and interior whitespace could be part of the value.

    An empty file is an error rather than an empty secret. `touch`ing the file
    and forgetting to fill it is the likeliest mistake of the lot, and an empty
    JWT key is not in DEFAULT_JWT_SECRETS, so it would sail past the startup
    guard and sign tokens with "".
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        raise RuntimeError(
            f"{var_name} points at {path!r}, which could not be read "
            f"({exc.__class__.__name__}: {exc.strerror or exc}). A "
            f"{SECRET_FILE_SUFFIX} variable is a promise that the secret is in "
            "that file; the server refuses to start rather than carry on with "
            "whatever the plain environment variable happened to hold. Check "
            "the compose `secrets:` mapping and the file's permissions."
        ) from exc

    value = raw.rstrip("\r\n")
    if not value:
        raise RuntimeError(
            f"{var_name} points at {path!r}, which is empty. Write the secret "
            "into that file (see docs/operations.md, 'Secrets') — an empty "
            "value would be accepted by the startup guards and silently used."
        )
    return value


class SecretFileSettingsSource(PydanticBaseSettingsSource):
    """Settings source for the `<NAME>_FILE` convention.

    Placed ahead of the environment source in `settings_customise_sources`, so
    `JWT_SECRET_KEY_FILE` beats a `JWT_SECRET_KEY` that is also set.

    Deliberately not pydantic-settings' own `secrets_dir`: that expects one
    directory of files named after the settings and gives no way to point two
    settings at differently-named files, and it silently ignores a missing one.
    """

    def get_field_value(self, field, field_name):  # pragma: no cover - unused
        # Required by the base class, but this source is driven by the env var
        # names in SECRET_FILE_ENV_VARS rather than by field iteration.
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        aliases = {
            field.alias
            for field in self.settings_cls.model_fields.values()
            if field.alias
        }
        values: dict[str, Any] = {}
        for name in SECRET_FILE_ENV_VARS:
            if name not in aliases:
                continue
            path = os.environ.get(name + SECRET_FILE_SUFFIX)
            if path is None or not path.strip():
                # Compose writes a commented-out secret as the empty string;
                # that means "unset", not "read the file called ''".
                continue
            # Keyed by alias: that is what pydantic-settings' own env source
            # returns for an aliased field, and Settings does not accept field
            # names on init.
            values[name] = read_secret_file(name + SECRET_FILE_SUFFIX, path.strip())
        return values


def build_database_url(
    *, user: str, password: str, host: str, port: int, database: str
) -> str:
    """The URL `docker-compose.example.yml` used to interpolate by hand.

    Compose can interpolate `${POSTGRES_PASSWORD}` into a URL; it cannot
    interpolate a *secret*, which only ever arrives as a file. So the server
    assembles the same URL itself when DATABASE_URL is not set explicitly.

    The password is percent-encoded: `token_urlsafe` output never needs it, but
    an operator-chosen password containing `@` or `/` would otherwise point the
    server at a different host or a different database.
    """
    return (
        f"postgresql+asyncpg://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{database}"
    )


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://booksync:booksync@localhost:5432/booksync",
        alias="DATABASE_URL",
    )

    # Parts the URL is assembled from when DATABASE_URL is *not* set explicitly
    # (issue #180). The defaults reproduce the line the compose template used to
    # interpolate by hand — `booksync:${POSTGRES_PASSWORD}@db:5432/booksync` —
    # so an install that moves the password into a docker secret keeps pointing
    # at exactly the same database.
    #
    # POSTGRES_PASSWORD is the only one that matters in practice: it is also the
    # variable the `db` service reads, so one secret file feeds both containers.
    # It is never a second source of truth for an existing deployment — an
    # explicit DATABASE_URL always wins (see _assemble_database_url below).
    postgres_user: str = Field(default="booksync", alias="POSTGRES_USER")
    postgres_password: str = Field(default="", alias="POSTGRES_PASSWORD")
    postgres_host: str = Field(default="db", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="booksync", alias="POSTGRES_DB")

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

    # Per-username login throttle (issue #296). Counts only FAILED logins in a
    # sliding window; a success clears the counter. Complements the per-IP
    # slowapi bucket, which docker NAT can collapse into one bucket (#294).
    # The limit is deliberately high and the window short: keying on the
    # username means an attacker who knows one can lock the real user out for
    # the window, so the threshold must sit far above any run of typos. See
    # rate_limit.FailedLoginTracker and docs/operations.md, "Login throttling".
    login_failure_limit: int = Field(default=10, alias="LOGIN_FAILURE_LIMIT")
    login_failure_window_seconds: int = Field(
        default=15 * 60, alias="LOGIN_FAILURE_WINDOW_SECONDS"
    )

    # Failed *current-password* checks on /auth/change-password (issue #264).
    # Much lower than the login limit above, and deliberately so: the reasoning
    # that forces login's threshold high does not apply here. Login keys on a
    # username anyone can name, so a low limit would let an attacker lock a real
    # user out. This bucket keys on the authenticated user's id, and reaching
    # the endpoint at all requires that user's own valid access token — so the
    # caller already *is* the session. Locking it is the point.
    password_change_failure_limit: int = Field(
        default=5, alias="PASSWORD_CHANGE_FAILURE_LIMIT"
    )
    password_change_failure_window_seconds: int = Field(
        default=15 * 60, alias="PASSWORD_CHANGE_FAILURE_WINDOW_SECONDS"
    )

    # Rejected /auth/refresh attempts for one token subject (issue #264). Only
    # failures count: a valid refresh is cheap, and the web client single-flights
    # them (#268). A rejected one costs a DB lookup, which is the abuse worth
    # bounding. Generous, because a client whose token legitimately expired will
    # retry a few times before its user gives up and signs in again.
    refresh_failure_limit: int = Field(default=20, alias="REFRESH_FAILURE_LIMIT")
    refresh_failure_window_seconds: int = Field(
        default=15 * 60, alias="REFRESH_FAILURE_WINDOW_SECONDS"
    )

    # ── Per-user rate limits on expensive reads (issue #208) ─────────────────
    #
    # Keyed on the authenticated user's id, not the client address: behind Caddy
    # and docker NAT every caller shares the proxy's address (#294), so an IP
    # bucket here would throttle the whole deployment together. Same reasoning,
    # and the same FailedLoginTracker machinery, as the /auth/change-password
    # bucket above — see rate_limit.UserRateLimiter and docs/operations.md,
    # "Rate limits on expensive reads".
    #
    # These count *every* request, not just failures, so they are much more
    # generous than the auth buckets. Each is set at least an order of magnitude
    # above what the UI actually does, so the ceiling is only ever reached by a
    # loop: the Troubleshoot and System pages each fire their reads once on load.

    # Endpoints that stat every library file, walk a data root or shell out:
    # /api/troubleshoot/issues, /api/library/verify, /api/stats/disk_usage,
    # /api/library/calibre-status. All four are also cached, so the limit is a
    # second line rather than the only one.
    expensive_read_limit: int = Field(default=30, alias="EXPENSIVE_READ_LIMIT")
    expensive_read_window_seconds: int = Field(
        default=60, alias="EXPENSIVE_READ_WINDOW_SECONDS"
    )

    # Library search and queue history: bounded queries, but still a DB round
    # trip per call that nothing else meters.
    search_read_limit: int = Field(default=60, alias="SEARCH_READ_LIMIT")
    search_read_window_seconds: int = Field(
        default=60, alias="SEARCH_READ_WINDOW_SECONDS"
    )

    # POST /api/library/match/search — the only bucket that meters something the
    # operator pays for rather than something the server computes. It spends the
    # admin-configured Google Books / Hardcover / Audible quota on any caller's
    # behalf, and quota exhaustion breaks matching for everyone until it resets,
    # so this one is the tightest of the three.
    external_metadata_search_limit: int = Field(
        default=20, alias="EXTERNAL_METADATA_SEARCH_LIMIT"
    )
    external_metadata_search_window_seconds: int = Field(
        default=60, alias="EXTERNAL_METADATA_SEARCH_WINDOW_SECONDS"
    )

    # ── TTL caches on the same endpoints (issues #208, #233) ─────────────────
    # A page that polls and a hostile loop should cost the same. Values are
    # seconds; 0 disables caching.

    # Recursive os.scandir over three data roots — the most expensive of them.
    disk_usage_cache_seconds: int = Field(default=300, alias="DISK_USAGE_CACHE_SECONDS")
    # `ebook-convert --version` in a subprocess; the answer changes on rebuild.
    calibre_status_cache_seconds: int = Field(
        default=300, alias="CALIBRE_STATUS_CACHE_SECONDS"
    )
    # Per-audiobook mutagen atom parse, keyed on (path, mtime, size) so an edited
    # file is re-checked immediately regardless of the TTL.
    chapter_encoding_cache_seconds: int = Field(
        default=300, alias="CHAPTER_ENCODING_CACHE_SECONDS"
    )
    # GET /api/health/backup (issue #233). Unauthenticated, so this cache is what
    # bounds it instead of a per-user bucket; well under any sane monitor's
    # interval, and staleness is measured in hours.
    backup_probe_cache_seconds: int = Field(
        default=60, alias="BACKUP_PROBE_CACHE_SECONDS"
    )

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
    # No `transcription_remote_timeout` here on purpose (issue #248): the queue
    # path reads the timeout from `system_settings` only, so an env field was a
    # second, silently-ignored source of truth advertising a default (7200)
    # that nothing used. The knob is System → Transcription → Remote Timeout.

    # Auto-transcribe
    auto_transcribe_enabled: bool = Field(default=False, alias="AUTO_TRANSCRIBE_ENABLED")

    # Per-file upload size caps (issue #151). Layered defense for uploads:
    #   1. the fronting proxy caps the raw request body at the edge
    #      (request_body in Caddyfile.example);
    #   2. UPLOAD_MAX_BYTES (below, issue #157) refuses a declared
    #      Content-Length over the whole-multipart-body cap BEFORE parsing
    #      (middleware.py);
    #   3. these caps bound each uploaded FILE while it streams to disk
    #      (services/uploads.py) — authoritative even for chunked or lying
    #      Content-Length, since the byte count, not the header, is enforced.
    # Keep the per-file cap ≤ the whole-body cap (UPLOAD_MAX_BYTES) or layer 3
    # can never be reached at its own limit.
    max_upload_file_bytes: int = Field(default=4 * 1024**3, alias="MAX_UPLOAD_FILE_BYTES")
    max_cover_bytes: int = Field(default=16 * 1024**2, alias="MAX_COVER_BYTES")

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

    # No `server_host` / `server_port` here on purpose (issue #182): nothing
    # outside this file ever read them. The listen address is uvicorn's, fixed
    # on its command line in `server/entrypoint.sh`
    # (`--host 0.0.0.0 --port 8000`) and remapped by the compose port binding,
    # so a setting of the same name only advertised a knob that did nothing.

    # CORS — comma-separated list of allowed origins; defaults to wildcard for dev
    cors_origins: str = Field(default="*", alias="CORS_ORIGINS")

    # Reverse-proxy trust (issue #156). uvicorn itself consumes this env var:
    # ProxyHeadersMiddleware rewrites scope["client"] from X-Forwarded-For only
    # when the socket peer is listed here (default trust list: 127.0.0.1).
    # The app never parses the header — it only mirrors the var to warn when a
    # prod deployment behind a proxy forgot to set it, which silently collapses
    # the auth rate limit into one global bucket. Never set this to "*".
    forwarded_allow_ips: Optional[str] = Field(default=None, alias="FORWARDED_ALLOW_IPS")

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

    @model_validator(mode="after")
    def _assemble_database_url(self) -> "Settings":
        """Build DATABASE_URL from the POSTGRES_* parts when it wasn't given.

        `model_fields_set` is what distinguishes "the operator set DATABASE_URL"
        from "the field fell back to its code default", so a deployment that
        already points at an external Postgres — a non-default sslmode, another
        host — never has its URL rebuilt out from under it.
        """
        if "database_url" in self.model_fields_set:
            return self
        if not self.postgres_password:
            return self
        self.database_url = build_database_url(
            user=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Insert the `<NAME>_FILE` source ahead of the environment (issue #180).

        Earlier sources win in pydantic-settings, so this *is* the precedence
        rule: explicit init kwargs first (tests), then `<NAME>_FILE`, then the
        plain environment, then `.env`.
        """
        return (
            init_settings,
            SecretFileSettingsSource(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

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


# Env vars that make an ASGI server fork extra workers. uvicorn reads
# WEB_CONCURRENCY (and honours --workers/UVICORN_WORKERS in some wrappers);
# gunicorn reads WEB_CONCURRENCY and GUNICORN_WORKERS.
WORKER_COUNT_ENV_VARS = ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS")


def check_single_process(s: "Settings") -> None:
    """Refuse to boot when the environment asks for more than one worker.

    The transcription pipeline is single-process by construction (issue #252):

    * ``services/queue_manager._process_next_item`` claims work with a plain
      ``SELECT ... LIMIT 1`` followed by an ``UPDATE`` in the same session — no
      row lock, so two processes claim the same item and transcribe the same
      audiobook twice.
    * Cancel/pause/active-job state (``_cancel_requested``, ``_pause_requested``,
      ``_active_provider``) lives in module-level Python sets, so a cancel only
      reaches the process that happens to own the job.
    * ``reset_stale_items()`` flips *every* ``in_progress`` row back to
      ``pending`` at startup, so a second process re-queues the first one's
      running job.
    * ``import_scheduler`` and ``backup_service`` are started inside the
      per-process lifespan, so N processes means N nightly backups and N
      concurrent import syncs.

    None of that is enforced anywhere else, and the failure mode is silent, so
    the mistake is caught here at boot instead. Making the server genuinely
    multi-process is a real redesign (atomic claim, cancel flags on the queue
    row, worker heartbeats) tracked separately — not something to enable by
    setting an env var.
    """
    import logging
    import os

    logger = logging.getLogger(__name__)
    for var in WORKER_COUNT_ENV_VARS:
        raw = os.environ.get(var)
        if raw is None or not raw.strip():
            continue
        try:
            workers = int(raw.strip())
        except ValueError:
            logger.warning(
                "%s=%r is not a number; ignoring it. The Tandem server must run "
                "as a single process — see the single-process note in "
                "docs/operations.md.",
                var,
                raw,
            )
            continue
        if workers > 1:
            raise RuntimeError(
                f"{var}={workers} would run the server as {workers} processes, "
                "but the transcription queue, cancellation state and the "
                "import/backup schedulers are single-process only: you would get "
                "duplicate transcriptions, cancels that reach the wrong process "
                "and jobs re-queued out from under each other. Unset it or set "
                f"{var}=1, and scale by giving this one process more CPU. See "
                "docs/operations.md ('Single process only') and issue #252."
            )


def check_forwarded_allow_ips(s: "Settings") -> None:
    """Warn (never raise) when prod runs without FORWARDED_ALLOW_IPS.

    Behind a reverse proxy every request shares one socket peer; unless uvicorn
    is told to trust that peer's X-Forwarded-For, the per-IP auth rate limit
    becomes one global bucket (five bad logins a minute lock everyone out) and
    audit rows record the proxy's address. Warn rather than refuse: a bare
    deployment with no proxy is still legitimate. See issue #156.
    """
    import logging

    logger = logging.getLogger(__name__)
    if s.app_env != "prod":
        return
    if s.forwarded_allow_ips and s.forwarded_allow_ips.strip():
        return
    logger.warning(
        "FORWARDED_ALLOW_IPS is unset. If this server sits behind a reverse "
        "proxy (Caddy, the shipped nginx web container), all clients share the "
        "proxy's IP: the login/register rate limit becomes one global bucket "
        "and audit logs record the proxy's address. Set FORWARDED_ALLOW_IPS to "
        "the address your proxy connects from (see docs/operations.md, "
        "'Reverse proxy')."
    )
