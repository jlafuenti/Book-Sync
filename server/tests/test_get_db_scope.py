"""
`get_db` commits before the response is sent (issue #524).

`get_db` is a `yield` dependency: the handler runs, then the code after the
`yield` commits. Since FastAPI 0.118 the code after a dependency's `yield`
runs *after the response has been sent* unless the dependency is declared
with `scope="function"` (added in 0.121). Under the default scope every
handler that relies on `get_db` for its commit — the documented default in
docs/request-transactions.md — answers the client before the row is
committed, so:

- a write followed by an immediate read returns the old state (observed
  live: `DELETE /api/library/pairs/{id}` → 204, then `GET` → 200 with the
  pair still there, then 404 a few seconds later);
- a commit that fails is reported as success, because the client already
  has its 2xx when the rollback happens.

The fix is `Depends(get_db, scope="function")` at every use site. That is a
one-token regression waiting to happen — a new router copies
`Depends(get_db)` from memory — so this walks the assembled app and checks
every dependant, including nested ones (`get_current_user` and friends
also take the session), rather than trusting a grep.
"""

from fastapi.routing import APIRoute

from database import get_db


def _walk(dependant, seen=None):
    """Every Dependant reachable from [dependant], depth first."""
    seen = set() if seen is None else seen
    for sub in dependant.dependencies:
        key = id(sub)
        if key in seen:
            continue
        seen.add(key)
        yield sub
        yield from _walk(sub, seen)


def _api_routes(routes):
    """APIRoutes under [routes], descending into routers FastAPI includes lazily.

    Since 0.140-ish `app.include_router` leaves an `_IncludedRouter` in
    `app.routes` rather than copying the routes in; its `original_router`
    holds the real APIRoutes (prefix-less paths, which is fine for naming).
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from _api_routes(route.original_router.routes)
        elif hasattr(route, "routes"):
            yield from _api_routes(route.routes)


def _db_dependants():
    from main import app

    for route in _api_routes(app.routes):
        for dep in _walk(route.dependant):
            if dep.call is get_db:
                yield route, dep


def test_the_app_actually_uses_get_db():
    """Guards the test below against becoming vacuous if the dependency is renamed."""
    routes = {route.path for route, _ in _db_dependants()}
    assert len(routes) > 20, f"expected get_db on most routes, found it on {sorted(routes)}"


def test_every_get_db_dependency_commits_before_the_response():
    wrong = sorted({
        f"{','.join(sorted(route.methods))} {route.path}"
        for route, dep in _db_dependants()
        if dep.scope != "function"
    })
    assert not wrong, (
        "These routes take `Depends(get_db)` without `scope=\"function\"`, so their "
        "commit runs after the response is sent (FastAPI >= 0.118) and a client "
        "that reads straight after writing sees the old state — issue #524. Use "
        "`Depends(get_db, scope=\"function\")` (see database.get_db and "
        "docs/request-transactions.md):\n  " + "\n  ".join(wrong)
    )
