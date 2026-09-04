"""
The committed OpenAPI export tracks the live app (issue #260).

`docs/openapi.json` is the API reference for anyone who is not running the
server in dev — the interactive Swagger UI is dev-only since issue #263. A
stale export is worse than none: it reads as authoritative while describing
routes that no longer exist.

So it gets the same drift-gate treatment as the Alembic migrations. CI
regenerates it and fails on a diff; this test is the local half, and it is the
one that fires in a normal `pytest` run before anything is pushed.

Regenerate with:

    python server/scripts/export_openapi.py
"""

import os

import pytest

from config import settings

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_SERVER_DIR)


@pytest.fixture(scope="module", autouse=True)
def _app_data_dir_for_main_import(tmp_path_factory):
    """Importing `main` creates `$APP_DATA_DIR/logs` at module scope, and the
    default (`/data/app`) is not writable in a test environment. Same fixture as
    `test_startup.py` — whichever module imports `main` first has to do this."""
    original = settings.app_data_dir
    settings.app_data_dir = str(tmp_path_factory.mktemp("app_data"))
    yield
    settings.app_data_dir = original


def _export_module():
    pytest.importorskip("audible")  # routers.import_sources needs it
    from scripts import export_openapi

    return export_openapi


def test_committed_openapi_matches_the_app():
    export_openapi = _export_module()

    expected = export_openapi.render(export_openapi.build_spec())
    actual = export_openapi.committed()

    assert actual, (
        "docs/openapi.json is missing. Generate it with "
        "`python server/scripts/export_openapi.py`."
    )
    assert actual == expected, (
        "docs/openapi.json no longer matches the app's schema. Regenerate it in "
        "the same commit that changed the routes:\n"
        "    python server/scripts/export_openapi.py"
    )


def test_export_path_is_the_committed_docs_file():
    export_openapi = _export_module()

    assert export_openapi.OUTPUT_PATH == (
        __import__("pathlib").Path(_REPO_ROOT) / "docs" / "openapi.json"
    )


def test_render_is_byte_stable():
    """An unstable dump would make the CI drift gate fire on every commit."""
    export_openapi = _export_module()

    spec = export_openapi.build_spec()
    assert export_openapi.render(spec) == export_openapi.render(spec)
    assert export_openapi.render(spec).endswith("\n")


def test_check_mode_passes_against_the_committed_file(capsys):
    export_openapi = _export_module()

    assert export_openapi.main_cli(["--check"]) == 0
    assert "up to date" in capsys.readouterr().out


def test_check_mode_fails_when_the_export_has_drifted(monkeypatch, capsys):
    export_openapi = _export_module()

    monkeypatch.setattr(
        export_openapi, "committed", lambda: '{"openapi": "from-an-older-commit"}\n'
    )

    assert export_openapi.main_cli(["--check"]) == 1
    assert "out of date" in capsys.readouterr().err


def test_write_mode_rewrites_the_file(tmp_path, monkeypatch, capsys):
    """The regenerate path, exercised without touching the real docs file."""
    export_openapi = _export_module()

    target = tmp_path / "nested" / "openapi.json"
    monkeypatch.setattr(export_openapi, "OUTPUT_PATH", target)

    assert export_openapi.main_cli([]) == 0
    assert "wrote" in capsys.readouterr().out
    assert target.read_text(encoding="utf-8") == export_openapi.render(
        export_openapi.build_spec()
    )


def test_the_spec_covers_the_whole_api_surface():
    """A truncated export would still be byte-stable — pin that it is the real
    document, with the auth and library families in it."""
    export_openapi = _export_module()

    paths = export_openapi.build_spec()["paths"]

    for path in (
        "/api/auth/login",
        "/api/auth/refresh",
        "/api/auth/logout",
        "/api/auth/logout-all",
        "/api/settings/",
        "/api/library/verify",
        "/api/health",
    ):
        assert path in paths, f"{path} missing from the exported schema"
