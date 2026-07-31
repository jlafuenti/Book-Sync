"""
Cross-language request-body contract test (issue #46, Phase 2).

The Android client hand-writes DTOs that must line up with the server's Pydantic
request models. When they drift, fields get silently dropped (Pydantic ignores
unknown request fields) — exactly what happened with `epub_locator`. This test
parses the Kotlin `@Serializable` request DTOs and asserts every field the client
sends exists on the mapped server model.

Only request bodies are checked (responses tolerate extra/missing fields via
kotlinx `ignoreUnknownKeys`). The mapping is curated on purpose.
"""

import os
import re

import pytest

from schemas import (
    BookPairCreate,
    BookmarkResponse,
    BookmarkUpdate,
    PasswordChange,
    ProgressUpdate,
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
    "BookmarkUpdateRequest": BookmarkUpdate,
    "ProgressUpdateRequest": ProgressUpdate,
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


@pytest.mark.parametrize("model", [BookmarkUpdate, BookmarkResponse],
                         ids=["request", "response"])
def test_precise_position_fields_are_pinned(model):
    """`epub_locator` was dropped once already (issue #40); `locator_audio_ms`
    is its audio anchor and is just as easy to lose. Both must survive on the
    request *and* response side, or exact cross-device resume silently
    degrades with no test failing."""
    fields = set(model.model_fields.keys())
    assert {"epub_locator", "locator_audio_ms"} <= fields


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
