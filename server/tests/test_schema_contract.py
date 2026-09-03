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
# Model <-> migration agreement on the library indexes (issue #256)
# ---------------------------------------------------------------------------
#
# `file_path` is the identity key the scan and the conversion path look rows up
# by, with `.scalar_one_or_none()`. Nothing in the schema enforced that until
# 0012: one duplicate row — a manual insert, a restore from an older dump, a
# future concurrent ingest — and the whole library scan 500s on
# `MultipleResultsFound`, exactly the failure #64 fixed for `user_progress`.
#
# `alembic check` is the real drift gate, but it only runs in the Postgres CI
# job and it compares *shapes*, not names. These assertions run in the default
# SQLite suite and pin the names, because the names are what the dedupe SQL, the
# downgrade, and any later `op.drop_index` have to agree on.

_MIGRATION_0012 = os.path.join(
    _SERVER_DIR, "alembic", "versions", "0012_book_file_path_indexes.py"
)

# index name -> (table, columns, unique)
EXPECTED_INDEXES = {
    "ux_ebooks_file_path": ("ebooks", ["file_path"], True),
    "ux_audiobooks_file_path": ("audiobooks", ["file_path"], True),
    "ix_ebooks_file_hash": ("ebooks", ["file_hash"], False),
    "ix_audiobooks_file_hash": ("audiobooks", ["file_hash"], False),
    "ix_book_pairs_ebook_id": ("book_pairs", ["ebook_id"], False),
    "ix_book_pairs_audiobook_id": ("book_pairs", ["audiobook_id"], False),
}

UQ_BOOK_PAIRS_PAIR = "uq_book_pairs_pair"


@pytest.fixture(scope="module")
def book_tables():
    from models.book import AudioBook, BookPair, EBook

    return {
        "ebooks": EBook.__table__,
        "audiobooks": AudioBook.__table__,
        "book_pairs": BookPair.__table__,
    }


@pytest.mark.parametrize("name", sorted(EXPECTED_INDEXES), ids=sorted(EXPECTED_INDEXES))
def test_library_index_is_declared_on_the_model(book_tables, name):
    table_name, columns, unique = EXPECTED_INDEXES[name]
    table = book_tables[table_name]

    index = next((ix for ix in table.indexes if ix.name == name), None)
    assert index is not None, (
        f"{table_name} declares no index named {name!r}; found "
        f"{sorted(ix.name for ix in table.indexes)}. The migration creates it "
        "under that name, so a rename here silently leaves production carrying "
        "an index the models no longer know about."
    )
    assert [c.name for c in index.columns] == columns
    assert index.unique is unique


def test_book_pairs_pair_is_unique_on_the_model(book_tables):
    """`create_pair` already 409s on a duplicate; this pins it in the schema."""
    from sqlalchemy import UniqueConstraint

    constraints = {
        c.name: sorted(col.name for col in c.columns)
        for c in book_tables["book_pairs"].constraints
        if isinstance(c, UniqueConstraint)
    }
    assert constraints.get(UQ_BOOK_PAIRS_PAIR) == ["audiobook_id", "ebook_id"], constraints


@pytest.mark.parametrize(
    "name", sorted(EXPECTED_INDEXES) + [UQ_BOOK_PAIRS_PAIR],
    ids=sorted(EXPECTED_INDEXES) + [UQ_BOOK_PAIRS_PAIR],
)
def test_the_migration_creates_each_name_the_models_declare(name):
    """Same names on both sides — SQLite tests build from the models, production
    builds from the migration, and only matching names make those the same
    database."""
    with open(_MIGRATION_0012, encoding="utf-8") as fh:
        source = fh.read()
    assert name in source, f"{name} is declared on a model but never created by 0012"


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
