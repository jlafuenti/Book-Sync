package com.booksync.data.remote

import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import java.io.IOException

/**
 * The cached answer to "does this server speak our API" (issue #174).
 *
 * The verdict is process-wide state rather than a ViewModel field for the same
 * reason [FirstRunGate] is: the screens that show it come and go, and asking the
 * server again on every recreation would put a network call behind a tab switch.
 */
class ServerVersionGateTest {

    private lateinit var api: BookSyncApi
    private lateinit var urls: ServerUrlManager

    @Before
    fun setUp() {
        api = mockk()
        urls = mockk()
        every { urls.currentUrl } returns "https://tandem.example.com"
    }

    private fun newGate() = ServerVersionGate(api, urls)

    @Test
    fun `it probes the configured server and keeps the verdict`() = runTest {
        coEvery { api.getHealth(any()) } returns HealthResponse(status = "healthy", api_version = 99)

        val gate = newGate()
        gate.refreshOnce()

        coVerify(exactly = 1) { api.getHealth("https://tandem.example.com/api/health") }
        assertEquals(VersionCompat.Verdict.ServerNewer, gate.verdict.value)
    }

    @Test
    fun `a second caller reuses the cached verdict instead of asking again`() = runTest {
        coEvery { api.getHealth(any()) } returns HealthResponse(api_version = SUPPORTED_API_VERSION)

        val gate = newGate()
        gate.refreshOnce()
        gate.refreshOnce()

        coVerify(exactly = 1) { api.getHealth(any()) }
        assertEquals(VersionCompat.Verdict.Ok, gate.verdict.value)
    }

    @Test
    fun `a server that answers without a version is Unknown and is not asked again`() = runTest {
        // Every deployment running today. Silence is an answer, and re-asking it
        // on every screen would be a network call per tab switch for nothing.
        coEvery { api.getHealth(any()) } returns HealthResponse(status = "healthy")

        val gate = newGate()
        gate.refreshOnce()
        gate.refreshOnce()

        coVerify(exactly = 1) { api.getHealth(any()) }
        assertEquals(VersionCompat.Verdict.Unknown, gate.verdict.value)
    }

    @Test
    fun `an unreachable server stays Unknown and is retried later`() = runTest {
        // Being offline is not a verdict. Caching it would mean an app that
        // launched on a train never checks again for the rest of the process.
        coEvery { api.getHealth(any()) } throws IOException("Unable to resolve host")

        val gate = newGate()
        gate.refreshOnce()
        assertEquals(VersionCompat.Verdict.Unknown, gate.verdict.value)

        coEvery { api.getHealth(any()) } returns HealthResponse(api_version = 0)
        gate.refreshOnce()

        assertEquals(VersionCompat.Verdict.ServerOlder, gate.verdict.value)
    }

    @Test
    fun `nothing is probed while no server is configured`() = runTest {
        every { urls.currentUrl } returns ""

        val gate = newGate()
        gate.refreshOnce()

        coVerify(exactly = 0) { api.getHealth(any()) }
        assertEquals(VersionCompat.Verdict.Unknown, gate.verdict.value)
    }

    @Test
    fun `the login screen's own probe settles it without a second request`() = runTest {
        // The first-run screen has already called `/api/health` on the address
        // the user typed — the one they are about to adopt. Asking again as soon
        // as Home appears would be the same question to the same server.
        val gate = newGate()
        gate.record(HealthResponse(status = "healthy", api_version = 99))
        assertEquals(VersionCompat.Verdict.ServerNewer, gate.verdict.value)

        gate.refreshOnce()

        coVerify(exactly = 0) { api.getHealth(any()) }
    }
}
