"""
Import-smoke test.

Importing every router module catches broken function signatures, bad imports,
and typos at collection time — cheaply. This is the guardrail that would have
caught the broken ``upload_ebook`` / ``upload_audiobook`` handlers that shipped
undetected (issue #46).

Routers are import-light: the heavy transcription deps (torch/whisper) live in
``services.transcription`` and are imported lazily, so importing the routers does
not pull them in. ``conftest`` has already pointed ``DATABASE_URL`` at SQLite, so
the module-level engine builds without the Postgres driver.
"""

import importlib

import pytest

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


@pytest.mark.parametrize("module_name", ROUTER_MODULES)
def test_router_module_imports(module_name):
    """Every router module imports without error and exposes an APIRouter."""
    if module_name == "routers.import_sources":
        # Pulls in services.import_sources -> the `audible` package, a prod dep
        # CI always installs but local envs may lack. Skip locally, enforce in CI.
        pytest.importorskip("audible")
    module = importlib.import_module(module_name)
    assert hasattr(module, "router"), f"{module_name} has no `router` attribute"


def test_every_router_is_registered_in_main():
    """
    Guard against a router existing but never being wired into the app. Every
    module in ROUTER_MODULES should be referenced by main.py's include_router
    calls (by module alias), so a new router can't silently ship unrouted.
    """
    import os

    server_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(server_dir, "main.py"), encoding="utf-8") as fh:
        main_src = fh.read()

    # main.py imports routers under short aliases (e.g. `from routers import auth`)
    # then calls app.include_router(<alias>.router). Assert each router's leaf
    # name shows up in an include_router call.
    for module_name in ROUTER_MODULES:
        leaf = module_name.split(".")[-1]
        assert f"{leaf}.router" in main_src or f"{leaf}_router.router" in main_src, (
            f"{module_name} is never registered via include_router in main.py"
        )
