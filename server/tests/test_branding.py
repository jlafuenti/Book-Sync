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


def test_api_title_is_tandem():
    pytest.importorskip("audible")
    import main

    assert main.app.title == "Tandem"


async def test_root_payload_names_tandem():
    pytest.importorskip("audible")
    import main

    body = await main.root()
    assert body["name"] == "Tandem"
