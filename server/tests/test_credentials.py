"""
Tests for services.credentials (issue #46, Phase 3).

Covers the Fernet encrypt/decrypt round-trip, MultiFernet key rotation, and the
DB-backed get/set/delete helpers.
"""

import pytest
from cryptography.fernet import Fernet, InvalidToken

from config import settings as app_settings
from services import credentials


@pytest.fixture
def keyA():
    return Fernet.generate_key().decode()


@pytest.fixture
def keyB():
    return Fernet.generate_key().decode()


def test_dev_fallback_key_is_valid(monkeypatch):
    """With CREDENTIAL_ENC_KEYS unset, the insecure dev fallback must still be a
    valid Fernet key (regression: _DEV_KEY used to be malformed and crashed)."""
    monkeypatch.setattr(app_settings, "credential_enc_keys", "")
    monkeypatch.setattr(app_settings, "app_env", "dev")
    assert credentials.decrypt(credentials.encrypt("zero-config")) == "zero-config"


def test_dev_key_rejected_in_prod(monkeypatch):
    """The insecure dev fallback must never be used in prod mode."""
    monkeypatch.setattr(app_settings, "credential_enc_keys", "")
    monkeypatch.setattr(app_settings, "app_env", "prod")
    with pytest.raises(RuntimeError):
        credentials.encrypt("should-not-work")


def test_invalid_key_rejected_in_prod(monkeypatch):
    """A typo'd/invalid Fernet key must fail loudly at startup in prod, not
    silently fall back to fewer keys than configured."""
    monkeypatch.setattr(app_settings, "credential_enc_keys", "not-a-valid-fernet-key")
    monkeypatch.setattr(app_settings, "app_env", "prod")
    with pytest.raises(RuntimeError):
        credentials.validate_startup()


def test_invalid_key_rejected_in_dev_too(monkeypatch):
    """A malformed key is a config typo, not a dev/prod posture choice — it must
    fail loudly regardless of APP_ENV, not silently drop to fewer keys."""
    monkeypatch.setattr(app_settings, "credential_enc_keys", "not-a-valid-fernet-key")
    monkeypatch.setattr(app_settings, "app_env", "dev")
    with pytest.raises(RuntimeError):
        credentials.validate_startup()


def test_encrypt_decrypt_roundtrip(monkeypatch, keyA):
    monkeypatch.setattr(app_settings, "credential_enc_keys", keyA)
    blob = credentials.encrypt("super-secret-token")
    assert blob != b"super-secret-token"
    assert credentials.decrypt(blob) == "super-secret-token"


def test_key_rotation_old_blob_still_decrypts(monkeypatch, keyA, keyB):
    # Written under key A...
    monkeypatch.setattr(app_settings, "credential_enc_keys", keyA)
    blob = credentials.encrypt("rotate-me")
    # ...still decrypts when a new key B is prepended (MultiFernet tries all).
    monkeypatch.setattr(app_settings, "credential_enc_keys", f"{keyB},{keyA}")
    assert credentials.decrypt(blob) == "rotate-me"


def test_decrypt_fails_when_key_removed(monkeypatch, keyA, keyB):
    monkeypatch.setattr(app_settings, "credential_enc_keys", keyA)
    blob = credentials.encrypt("gone")
    monkeypatch.setattr(app_settings, "credential_enc_keys", keyB)
    with pytest.raises(InvalidToken):
        credentials.decrypt(blob)


async def test_set_get_delete_credential(monkeypatch, keyA, db):
    monkeypatch.setattr(app_settings, "credential_enc_keys", keyA)
    await credentials.set_credential(db, "abs", "abs-token")
    await db.commit()

    assert await credentials.get_credential(db, "abs") == "abs-token"

    # Upsert overwrites in place.
    await credentials.set_credential(db, "abs", "new-token")
    await db.commit()
    assert await credentials.get_credential(db, "abs") == "new-token"

    await credentials.delete_credential(db, "abs")
    await db.commit()
    assert await credentials.get_credential(db, "abs") is None


async def test_get_credential_returns_none_when_undecryptable(monkeypatch, keyA, keyB, db):
    monkeypatch.setattr(app_settings, "credential_enc_keys", keyA)
    await credentials.set_credential(db, "abs", "token")
    await db.commit()
    # Rotate to a key that can't decrypt the stored blob -> None, not a crash.
    monkeypatch.setattr(app_settings, "credential_enc_keys", keyB)
    assert await credentials.get_credential(db, "abs") is None
