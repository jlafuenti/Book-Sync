"""
Product-name contract (issue #58).

The product answered to two names — "Tandem" in the web UI and Android launcher,
"BookSync" in the API title, the `GET /` payload and the logs. Tandem is the
canonical name; these pin the two API surfaces that a user actually reads
(the Swagger page title and the root health payload) so they can't drift back.

Internal identifiers (`com.booksync`, the `booksync` Postgres role/database,
`booksync_db` volumes, `booksync-db-*.dump` backups) are deliberately *not*
covered here — renaming those costs a migration and buys nothing.
"""

import pytest

from config import settings


# Importing main for real runs, at module scope,
# `os.makedirs(settings.app_data_dir + "/logs")` — and app_data_dir defaults to
# "/data/app", which is not writable on a Linux CI runner (it silently succeeds
# on Windows, which is why this only failed in CI). Patch it to a tmp dir before
# the first import happens.
#
# test_startup.py carries the same fixture for the same reason. It has to be
# duplicated rather than shared: it is module-scoped, test order is randomized,
# and `import main` is cached in sys.modules — so whichever of the two modules
# runs first is the one that triggers the real import, and both must be safe.
@pytest.fixture(scope="module", autouse=True)
def _app_data_dir_for_main_import(tmp_path_factory):
    original = settings.app_data_dir
    settings.app_data_dir = str(tmp_path_factory.mktemp("app_data"))
    yield
    settings.app_data_dir = original


def test_api_title_is_tandem():
    pytest.importorskip("audible")
    import main

    assert main.app.title == "Tandem"


async def test_root_payload_names_tandem():
    pytest.importorskip("audible")
    import main

    body = await main.root()
    assert body["name"] == "Tandem"
