"""
Response-model ratchet for the API surface (issue #258).

Most routes hand-build dicts with no declared ``response_model``. Nothing
validates their shape on the way out, ``/openapi.json`` shows them as untyped,
and the web client reads keys that no server test guards — so renaming a dict
key is a silent breaking change that surfaces in the browser.

Typing all 124 routes in one pass is not worth the risk. Instead this test is a
ratchet, the same shape as the coverage floor in ``docs/testing.md``:

* every route under ``/api`` must declare a ``response_model``, **unless** it is
  named in ``UNTYPED_ALLOWLIST`` below;
* the allow-list must not contain routes that *are* typed.

So a new untyped route fails here immediately, and typing an existing one fails
here until its entry is deleted. The list only ever shrinks.
"""

import importlib

import pytest
from fastapi.routing import APIRoute

# Same set as tests/test_import_smoke.py — every router main.py mounts.
ROUTER_MODULES = [
    "routers.auth",
    "routers.chapters",
    "routers.files",
    "routers.import_sources",
    "routers.library",
    "routers.match",
    "routers.settings",
    "routers.stats",
    "routers.sync",
    "routers.transcription",
    "routers.troubleshoot",
    "routers.users",
]

# Routes that still return an undeclared hand-built dict. Seeded from the state
# of the tree when issue #258 was opened (69 routes), minus the ones typed since.
#
# **Only ever remove entries from this list.** Adding one means shipping a new
# untyped response; type it instead.
#
# A few entries are permanent rather than debt — they stream bytes
# (`FileResponse`/`StreamingResponse`), which has no JSON shape to declare; they
# are marked below.
UNTYPED_ALLOWLIST = {
    # --- auth -------------------------------------------------------------
    "POST /api/auth/change-password",
    # --- files (permanent: raw file/stream responses) ----------------------
    "GET /api/files/audiobook/{audiobook_id}",
    "GET /api/files/covers/{filename}",
    "GET /api/files/ebook/{ebook_id}",
    # --- import sources ----------------------------------------------------
    "GET /api/import/acsm/authorization",
    "GET /api/import/sources/{source_key}/jobs",
    "POST /api/import/acsm/authorize",
    "POST /api/import/acsm/deauthorize",
    "POST /api/import/acsm/upload",
    "POST /api/import/audible/disconnect",
    "POST /api/import/audible/login/complete",
    "POST /api/import/sources/{source_key}/sync",
    "PUT /api/import/sources/{source_key}/config",
    # --- library -----------------------------------------------------------
    # (the three 204 DELETEs that used to sit here are exempt now — see
    # _needs_a_response_model)
    "DELETE /api/library/unsupported/force-all",
    "DELETE /api/library/unsupported/{ebook_id}/force",
    "DELETE /api/library/unsupported/{ebook_id}/source",
    "GET /api/library/calibre-status",
    "GET /api/library/debug-metadata/{book_type}/{book_id}",
    "GET /api/library/verify",
    "POST /api/library/audiobooks/{audiobook_id}/enrich-abs",
    "POST /api/library/cleanup",
    "POST /api/library/enrich-abs",
    "POST /api/library/new-items/acknowledge",
    "POST /api/library/new-pairs/acknowledge",
    "POST /api/library/normalize",
    "POST /api/library/pairs/{pair_id}/ignore-discrepancies",
    "POST /api/library/pairs/{pair_id}/resolve-discrepancies",
    "POST /api/library/rehash",
    "POST /api/library/rescan-all",
    "POST /api/library/unsupported/convert-all",
    "POST /api/library/unsupported/{ebook_id}/convert",
    "POST /api/library/{book_type}s/{book_id}/rescan",
    # --- settings ----------------------------------------------------------
    "POST /api/settings/test-abs",
    "POST /api/settings/test-hardcover",
    "POST /api/settings/test-remote",
    "POST /api/settings/transcription-remote-key/generate",
    # --- stats -------------------------------------------------------------
    "DELETE /api/stats/backups/{backup_id}",
    "GET /api/stats/backups/{backup_id}/download",  # permanent: streams a dump
    "POST /api/stats/restore",
    # --- sync --------------------------------------------------------------
    "DELETE /api/sync/position/{scope}/{ident}",
    "DELETE /api/sync/progress/pair/{pair_id}",
    # --- transcription -----------------------------------------------------
    "DELETE /api/transcription/queue/{item_id}",
    "POST /api/transcription/{pair_id}/cancel",
    "POST /api/transcription/{pair_id}/realign",
    "PUT /api/transcription/queue/{item_id}/priority",
    "PUT /api/transcription/{pair_id}/text",
    # --- troubleshoot ------------------------------------------------------
    "GET /api/troubleshoot/issues",
    "GET /api/troubleshoot/scan/progress",
    "GET /api/troubleshoot/sync-map-audit",
    "POST /api/troubleshoot/acsm-dismiss",
    "POST /api/troubleshoot/bulk-delete",
    "POST /api/troubleshoot/bulk-repair-chapter-encoding",
    "POST /api/troubleshoot/delete-orphan-covers",
    "POST /api/troubleshoot/multi-file/{folder_id}/dismiss",
    "POST /api/troubleshoot/multi-file/{folder_id}/remove-tracks",
    "POST /api/troubleshoot/repair-chapter-encoding/{item_id}",
    "POST /api/troubleshoot/replace/{item_type}/{item_id}",
    "POST /api/troubleshoot/requeue/{pair_id}",
    "POST /api/troubleshoot/scan",
    "POST /api/troubleshoot/scan/cancel",
    # --- users -------------------------------------------------------------
    "DELETE /api/users/{user_id}",
    "GET /api/users/audit-log",
    "POST /api/users/{user_id}/reset-password",
}


def _route_key(route: APIRoute) -> str:
    methods = ",".join(sorted(route.methods - {"HEAD", "OPTIONS"}))
    return f"{methods} {route.path}"


def _api_routes() -> list[APIRoute]:
    """Every ``/api`` route across the routers main.py mounts.

    Read straight off the ``APIRouter`` objects rather than off a built app:
    each router already carries its own ``prefix`` in ``route.path``, and recent
    FastAPI keeps included routers nested under a mount rather than flattening
    them into ``app.routes``, so walking the app is the fragile spelling.
    """
    pytest.importorskip("audible")  # routers.import_sources needs it

    routes: list[APIRoute] = []
    for name in ROUTER_MODULES:
        for route in importlib.import_module(name).router.routes:
            if isinstance(route, APIRoute) and route.path.startswith("/api"):
                routes.append(route)
    return routes


def _needs_a_response_model(route: APIRoute) -> bool:
    """204 routes are exempt: there is no body, so there is no shape to declare.

    FastAPI refuses a ``response_model`` on a 204 outright, so allow-listing one
    would be recording debt that cannot be paid off — unlike the entries above,
    which are hand-built dicts waiting for a schema.
    """
    return route.response_model is None and route.status_code != 204


def test_no_new_untyped_routes():
    """Every /api route declares a response_model, or is grandfathered in."""
    untyped = {_route_key(r) for r in _api_routes() if _needs_a_response_model(r)}
    new = sorted(untyped - UNTYPED_ALLOWLIST)
    assert not new, (
        "These /api routes declare no response_model. Add one (see "
        "server/schemas.py) rather than extending UNTYPED_ALLOWLIST:\n  "
        + "\n  ".join(new)
    )


def test_allowlist_has_no_stale_entries():
    """Typing a route means deleting its allow-list entry, so the list shrinks."""
    routes = _api_routes()
    untyped = {_route_key(r) for r in routes if _needs_a_response_model(r)}
    known = {_route_key(r) for r in routes}

    stale = sorted(UNTYPED_ALLOWLIST - untyped)
    assert not stale, (
        "These routes now declare a response_model (or no longer exist). "
        "Delete them from UNTYPED_ALLOWLIST:\n  " + "\n  ".join(stale)
    )
    unknown = sorted(UNTYPED_ALLOWLIST - known)
    assert not unknown, (
        "UNTYPED_ALLOWLIST names routes that do not exist:\n  " + "\n  ".join(unknown)
    )
