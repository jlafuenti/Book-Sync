package com.booksync.data.remote

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The client/server API-version handshake (issue #174).
 *
 * A Play install updates on the user's schedule and a self-hosted server on the
 * operator's, so the two drift. Before this, drift showed up as a 404 or a
 * deserialisation failure with nothing saying which side was stale — and the
 * check cannot be retrofitted into clients that are already installed, which is
 * why it has to ship in the first release.
 *
 * Every case here is a verdict about *who* is behind, because that is the only
 * thing the two banners differ on: "update the app" and "upgrade the server"
 * are opposite instructions, and sending the wrong one wastes the user's time
 * on a change that cannot help.
 */
class VersionCompatTest {

    @Test
    fun `a server on the same API version is fine`() {
        assertEquals(VersionCompat.Verdict.Ok, VersionCompat.compare(serverApi = 3, clientApi = 3))
    }

    @Test
    fun `a server ahead of the client is ServerNewer`() {
        assertEquals(
            VersionCompat.Verdict.ServerNewer,
            VersionCompat.compare(serverApi = 4, clientApi = 3),
        )
    }

    @Test
    fun `a server behind the client is ServerOlder`() {
        assertEquals(
            VersionCompat.Verdict.ServerOlder,
            VersionCompat.compare(serverApi = 2, clientApi = 3),
        )
    }

    @Test
    fun `a server that reports no API version is Unknown`() {
        // Every deployment running today, and every one that predates issue #174:
        // `/api/health` answers `{"status":"healthy"}` and nothing else. Silence
        // is not a mismatch, and warning about it would put a banner in front of
        // every existing user for a problem they do not have.
        assertEquals(
            VersionCompat.Verdict.Unknown,
            VersionCompat.compare(serverApi = null, clientApi = 3),
        )
    }

    @Test
    fun `compare defaults to the version this build was written against`() {
        assertEquals(
            VersionCompat.Verdict.Ok,
            VersionCompat.compare(serverApi = SUPPORTED_API_VERSION),
        )
    }

    @Test
    fun `only a mismatch produces a banner`() {
        assertEquals(null, VersionCompat.bannerFor(VersionCompat.Verdict.Ok))
        assertEquals(null, VersionCompat.bannerFor(VersionCompat.Verdict.Unknown))
        assertEquals(
            VersionBanner.SERVER_NEWER,
            VersionCompat.bannerFor(VersionCompat.Verdict.ServerNewer),
        )
        assertEquals(
            VersionBanner.SERVER_OLDER,
            VersionCompat.bannerFor(VersionCompat.Verdict.ServerOlder),
        )
    }
}
