"""
Cross-language request-body contract test (issue #46, Phase 2).

The Android client hand-writes DTOs that must line up with the server's Pydantic
request models. When they drift, fields get silently dropped (Pydantic ignores
unknown request fields) — exactly what happened with the precise EPUB locator
(issue #40). This test parses the Kotlin `@Serializable` request DTOs and
asserts every field the client sends exists on the mapped server model.

Only request bodies are checked (responses tolerate extra/missing fields via
kotlinx `ignoreUnknownKeys`). The mapping is curated on purpose.
"""

import os
import re

import pytest

from schemas import (
    BookPairCreate,
    PasswordChange,
    PositionHintPayload,
    PositionHintResponse,
    PositionUpdate,
    TokenRefresh,
    UserCreate,
    UserLogin,
    UserUpdateRequest,
)

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_SERVER_DIR)
_APIDTOS = os.path.join(
    _REPO_ROOT, "android", "app", "src", "main", "java", "com",
    "booksync", "data", "remote", "dto", "ApiDtos.kt",
)

# Kotlin request DTO -> server Pydantic model. Request bodies only.
REQUEST_MAP = {
    "LoginRequest": UserLogin,
    "RegisterRequest": UserCreate,
    "RefreshRequest": TokenRefresh,
    "PasswordChangeRequest": PasswordChange,
    "UpdateMeRequest": UserUpdateRequest,
    "CreatePairRequest": BookPairCreate,
    "PositionUpdateRequest": PositionUpdate,
    "PositionHintDto": PositionHintPayload,
}


def _parse_kotlin_dtos(src: str) -> dict[str, set[str]]:
    """
    Return {data-class-name: {field names}}. Uses balanced-paren scanning so
    default values containing parens (e.g. `= emptyList()`) don't truncate the
    constructor body.
    """
    dtos: dict[str, set[str]] = {}
    for m in re.finditer(r"data class (\w+)\s*\(", src):
        name = m.group(1)
        i = m.end() - 1  # index of the opening '('
        depth = 0
        while i < len(src):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = src[m.end():i]
        dtos[name] = set(re.findall(r"\bval\s+(\w+)\s*:", body))
    return dtos


@pytest.fixture(scope="module")
def kotlin_dtos():
    with open(_APIDTOS, encoding="utf-8") as fh:
        dtos = _parse_kotlin_dtos(fh.read())
    assert dtos, "Parsed no DTOs from ApiDtos.kt — parser or path is wrong"
    return dtos


def test_all_mapped_request_dtos_exist(kotlin_dtos):
    """Guard against a client DTO being renamed out from under this test."""
    missing = [name for name in REQUEST_MAP if name not in kotlin_dtos]
    assert not missing, f"Mapped Kotlin request DTOs not found in ApiDtos.kt: {missing}"


@pytest.mark.parametrize("model", [PositionHintPayload, PositionHintResponse],
                         ids=["request", "response"])
def test_precise_position_fields_are_pinned(model):
    """The precise locator was dropped from the wire once already (issue #40)
    and its audio anchor is just as easy to lose. It now travels as a position
    hint rather than the `epub_locator`/`locator_audio_ms` columns (issue
    #102), so pin the hint's shape on the request *and* response side — without
    `value` + `audio_position_ms` surviving both, exact cross-device resume
    silently degrades with no test failing."""
    fields = set(model.model_fields.keys())
    assert {"kind", "value", "audio_position_ms"} <= fields


@pytest.mark.parametrize("kotlin_name,model", REQUEST_MAP.items(), ids=list(REQUEST_MAP))
def test_client_request_fields_exist_on_server_model(kotlin_dtos, kotlin_name, model):
    """Every field the client sends must exist on the server request model."""
    client_fields = kotlin_dtos[kotlin_name]
    server_fields = set(model.model_fields.keys())
    dropped = client_fields - server_fields
    assert not dropped, (
        f"{kotlin_name} sends field(s) {sorted(dropped)} that server "
        f"{model.__name__} does not accept (silently dropped)."
    )


# ---------------------------------------------------------------------------
# ON DELETE contract (issue #198)
#
# The ORM cascade and the database's own FK action have to agree, and Alembic
# autogenerate does not diff `ondelete` — a hand-written migration set these,
# and nothing but a test stops the model and the migration drifting apart
# again. Reflected from the live test schema, so this asserts what the database
# actually has, not what the declaration says.
#
# `audit_logs` is deliberately NOT in this list: history must survive the user,
# so its `user_id` stays `SET NULL`.
# ---------------------------------------------------------------------------

_CASCADING_USER_FKS = [("user_progress", "user_id"), ("bookmarks", "user_id")]


async def _reflected_fks(table):
    from sqlalchemy import inspect

    from database import engine

    async with engine.connect() as conn:
        return await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_foreign_keys(table)
        )


@pytest.mark.parametrize("table,column", _CASCADING_USER_FKS,
                         ids=[t for t, _ in _CASCADING_USER_FKS])
async def test_user_child_fks_cascade_on_delete(table, column):
    fks = [fk for fk in await _reflected_fks(table)
           if fk["constrained_columns"] == [column]
           and fk["referred_table"] == "users"]
    assert len(fks) == 1, f"expected one {table}.{column} -> users.id FK, got {fks}"
    assert fks[0].get("options", {}).get("ondelete", "").upper() == "CASCADE", (
        f"{table}.{column} must be ON DELETE CASCADE — deleting a user who has "
        f"read anything 500s on Postgres otherwise (issue #198)"
    )


async def test_audit_log_user_fk_is_set_null_not_cascade():
    """Deleting an account must not erase the audit trail of what it did."""
    fks = [fk for fk in await _reflected_fks("audit_logs")
           if fk["referred_table"] == "users"]
    assert fks
    for fk in fks:
        assert fk.get("options", {}).get("ondelete", "").upper() == "SET NULL"
