"""
Who can read the admin console's data (issue #283).

Any authenticated user could load `/system` and have the page fire its
admin-flavoured reads. Every *mutating* endpoint was already gated, so no
privilege was gained — but a plain reader saw disk capacity for all three data
roots, backup health, the unsupported-file list, the troubleshoot issue list,
and internal hostnames from the settings map. Defence in depth, and infra-info
disclosure that matters more once the repo is public.

The two endpoints here are gated at **editor**, not admin, unlike the two
infrastructure reads in `test_stats.py`. Both are library maintenance: the
unsupported list drives conversion, and `TroubleshootPage`'s own edit controls
are already `hasMinRole('editor')`, so gating its data at admin would have shut
editors out of a page built for them.

Android calls neither endpoint — `BookSyncApi.kt` declares no `unsupported` or
`troubleshoot` route, and Retrofit cannot call what the interface does not
declare — so tightening these cannot break the app.
"""

import pytest

from routers import library, troubleshoot


@pytest.mark.parametrize("role", ["user"])
async def test_unsupported_list_refuses_below_editor(
    make_client, make_user, auth_header, role
):
    user = await make_user(username=f"u_{role}", role=role)
    async with make_client(library.router) as c:
        r = await c.get("/api/library/unsupported", headers=auth_header(user))
    assert r.status_code == 403


@pytest.mark.parametrize("role", ["editor", "admin", "superadmin"])
async def test_unsupported_list_allows_editor_and_above(
    make_client, make_user, auth_header, role
):
    user = await make_user(username=f"ok_{role}", role=role)
    async with make_client(library.router) as c:
        r = await c.get("/api/library/unsupported", headers=auth_header(user))
    assert r.status_code == 200


@pytest.mark.parametrize("role", ["user"])
async def test_troubleshoot_issues_refuses_below_editor(
    make_client, make_user, auth_header, role
):
    user = await make_user(username=f"t_{role}", role=role)
    async with make_client(troubleshoot.router) as c:
        r = await c.get("/api/troubleshoot/issues", headers=auth_header(user))
    assert r.status_code == 403


@pytest.mark.parametrize("role", ["editor", "admin"])
async def test_troubleshoot_issues_allows_editor_and_above(
    make_client, make_user, auth_header, role
):
    # Editor specifically: TroubleshootPage's fix controls are editor-gated, so
    # its data has to be readable at the same level or the page is useless to
    # exactly the role it was built for.
    user = await make_user(username=f"tok_{role}", role=role)
    async with make_client(troubleshoot.router) as c:
        r = await c.get("/api/troubleshoot/issues", headers=auth_header(user))
    assert r.status_code == 200
