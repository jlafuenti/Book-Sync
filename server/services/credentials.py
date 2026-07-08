"""
Encrypted-at-rest credential store for external integrations.

Uses cryptography.fernet.MultiFernet so that key rotation is just
"prepend a new key to CREDENTIAL_ENC_KEYS" — old blobs stay readable
because MultiFernet tries every key when decrypting; new writes use
the first key.
"""

import base64
import logging
from typing import Optional

from cryptography.fernet import Fernet, MultiFernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.import_source import ImportSourceCredential

logger = logging.getLogger(__name__)


# A fixed, INSECURE fallback key so zero-config local dev works. A valid Fernet
# key is urlsafe-base64 of exactly 32 bytes; the seed below is 32 ASCII bytes.
# Real deployments MUST set CREDENTIAL_ENC_KEYS (see the warning in _build_fernet).
_DEV_KEY = base64.urlsafe_b64encode(b"booksync-insecure-dev-key-000000")
_warned_default = False


_KEY_GEN_HINT = (
    "Generate one with: python -c 'from cryptography.fernet import Fernet; "
    "print(Fernet.generate_key().decode())' and set CREDENTIAL_ENC_KEYS=<key> "
    "in your environment."
)


def _build_fernet() -> MultiFernet:
    global _warned_default
    raw = (settings.credential_enc_keys or "").strip()
    if not raw:
        if settings.app_env == "prod":
            raise RuntimeError(
                "CREDENTIAL_ENC_KEYS is unset — refusing to start in prod. " + _KEY_GEN_HINT
            )
        if not _warned_default:
            logger.warning(
                "CREDENTIAL_ENC_KEYS not set — using insecure default key (dev mode). "
                + _KEY_GEN_HINT
            )
            _warned_default = True
        return MultiFernet([Fernet(_DEV_KEY)])

    keys = [k.strip() for k in raw.split(",") if k.strip()]
    fernets = []
    invalid = []
    for k in keys:
        try:
            fernets.append(Fernet(k.encode() if isinstance(k, str) else k))
        except Exception as e:
            invalid.append(k)
            logger.error(f"Invalid Fernet key in CREDENTIAL_ENC_KEYS: {e}")
    # A malformed key is a config typo, not a dev/prod security-posture choice —
    # fail fast in both modes rather than silently falling back to fewer keys.
    if invalid:
        raise RuntimeError(
            f"CREDENTIAL_ENC_KEYS contains {len(invalid)} invalid Fernet key(s) — "
            f"refusing to start. " + _KEY_GEN_HINT
        )
    if not fernets:
        raise RuntimeError("CREDENTIAL_ENC_KEYS contained no valid Fernet keys")
    return MultiFernet(fernets)


def validate_startup() -> None:
    """Eagerly build the Fernet keyset so startup fails fast instead of on
    first credential read."""
    _build_fernet()


def encrypt(plaintext: str) -> bytes:
    return _build_fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(blob: bytes) -> str:
    return _build_fernet().decrypt(blob).decode("utf-8")


async def get_credential(db: AsyncSession, source_key: str) -> Optional[str]:
    """Return the decrypted credential blob for the given source, or None."""
    result = await db.execute(
        select(ImportSourceCredential).where(ImportSourceCredential.source_key == source_key)
    )
    row = result.scalar_one_or_none()
    if not row or not row.blob_encrypted:
        return None
    try:
        return decrypt(row.blob_encrypted)
    except InvalidToken:
        logger.error(
            f"Could not decrypt credential for source '{source_key}' — "
            f"likely CREDENTIAL_ENC_KEYS no longer contains the key it was written with."
        )
        return None


async def set_credential(db: AsyncSession, source_key: str, plaintext: str) -> None:
    """Upsert an encrypted credential blob for the given source."""
    blob = encrypt(plaintext)
    result = await db.execute(
        select(ImportSourceCredential).where(ImportSourceCredential.source_key == source_key)
    )
    row = result.scalar_one_or_none()
    if row:
        row.blob_encrypted = blob
    else:
        db.add(ImportSourceCredential(source_key=source_key, blob_encrypted=blob))


async def delete_credential(db: AsyncSession, source_key: str) -> None:
    result = await db.execute(
        select(ImportSourceCredential).where(ImportSourceCredential.source_key == source_key)
    )
    row = result.scalar_one_or_none()
    if row:
        await db.delete(row)
