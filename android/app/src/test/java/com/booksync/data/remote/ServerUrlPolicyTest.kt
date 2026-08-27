package com.booksync.data.remote

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #58: the shipped server URL moved out of the source and into a build
 * property that defaults to empty, so a clean clone no longer points at anyone's
 * personal host. Two things have to hold for a build with no default:
 *
 *  - Retrofit still constructs (it rejects an empty base URL outright), and
 *  - the login screen opens with its "Advanced" server-URL field already visible,
 *    otherwise there is no discoverable way to enter one.
 */
class ServerUrlPolicyTest {

    @Test
    fun `blank stored url falls back to a constructible placeholder`() {
        assertEquals(UNCONFIGURED_BASE_URL, retrofitBaseUrl(""))
        assertEquals(UNCONFIGURED_BASE_URL, retrofitBaseUrl("   "))
        // Whatever the placeholder is, Retrofit requires a trailing slash.
        assertTrue(UNCONFIGURED_BASE_URL.endsWith("/"))
    }

    @Test
    fun `a configured url is trailing-slashed exactly once`() {
        assertEquals("https://host/", retrofitBaseUrl("https://host"))
        assertEquals("https://host/", retrofitBaseUrl("https://host/"))
        assertEquals("https://host:8443/", retrofitBaseUrl("https://host:8443///"))
    }

    @Test
    fun `advanced section starts expanded only when no server is configured`() {
        assertTrue(shouldExpandAdvanced(""))
        assertTrue(shouldExpandAdvanced("   "))
        assertFalse(shouldExpandAdvanced("https://host"))
    }

    // -- Issue #149: normalizeServerUrl ------------------------------------
    //
    // Everything below exists because an unvalidated server URL was a permanent
    // first-run lockout: the value persisted, the app restarted, and Retrofit
    // then threw inside Hilt on every launch with no UI to fix it from.

    // The IPv4 samples use 203.0.113.x (RFC 5737 TEST-NET-3, the documentation
    // range) rather than a realistic 192.168.x.y. Not cosmetic: the repo-wide
    // guard in server/tests/test_android_no_personal_hosts.py fails the build on
    // an RFC1918 literal anywhere in the Android sources, tests included. The
    // parser does not care which address this is.
    @Test
    fun `a bare host gets https rather than being rejected`() {
        // What a real user types on first run. Rejecting these would be correct
        // and useless; https is the right guess.
        assertEquals("https://tandem.example.com", normalizeServerUrl("tandem.example.com"))
        assertEquals("https://203.0.113.5:8000", normalizeServerUrl("203.0.113.5:8000"))
        assertEquals("https://host", normalizeServerUrl("  host  "))
    }

    @Test
    fun `an explicit scheme is preserved`() {
        assertEquals("http://203.0.113.5:8000", normalizeServerUrl("http://203.0.113.5:8000"))
        assertEquals("https://host:8443", normalizeServerUrl("https://host:8443"))
    }

    @Test
    fun `a sub-path is preserved, but query, fragment and credentials are dropped`() {
        // The path has to survive: `retrofitBaseUrl` used to be
        // `stored.trimEnd('/') + "/"`, which kept it, so anyone reverse-proxying
        // Tandem under a sub-path has one stored. Dropping it on upgrade would
        // break every request with no error and nothing visibly different.
        assertEquals("https://host/tandem", normalizeServerUrl("https://host/tandem"))
        assertEquals("https://host/tandem", normalizeServerUrl("https://host/tandem/"))
        // Query/fragment/credentials are not part of a base URL and never useful here.
        assertEquals("https://host", normalizeServerUrl("https://host/?a=b#frag"))
        assertEquals("https://host", normalizeServerUrl("https://user:pw@host"))
        assertEquals("https://host/x", normalizeServerUrl("https://host/a/../x"))
    }

    @Test
    fun `a malformed or foreign scheme is rejected rather than read as a hostname`() {
        // `contains("://")` was the wrong test: a single-slash typo has no "://",
        // so "https://" got prepended to the whole string and OkHttp read
        // everything up to the first "/" as the authority — "https:/host" became
        // a host literally named "https". That saved, restarted, and left the user
        // staring at a URL they never typed with no error to explain it.
        assertNull(normalizeServerUrl("https:/host"))
        assertNull(normalizeServerUrl("http:/host"))
        assertNull(normalizeServerUrl("https//host"))
        assertNull(normalizeServerUrl("HTTPS:/host"))
        assertNull(normalizeServerUrl("https:/203.0.113.5:8000"))
        assertNull(normalizeServerUrl("https:host"))
    }

    @Test
    fun `a bare host with a port or a path is not mistaken for a scheme`() {
        // The counterweight to the test above: "host.com:8000" is a syntactically
        // valid scheme followed by an opaque part, and must still be read as
        // host:port. A digit after the colon is what separates the two.
        assertEquals("https://host.com:8000", normalizeServerUrl("host.com:8000"))
        assertEquals("https://host.com/tandem", normalizeServerUrl("host.com/tandem"))
        assertEquals("https://203.0.113.5:8000", normalizeServerUrl("203.0.113.5:8000"))
    }

    @Test
    fun `an uppercase scheme is accepted and normalised`() {
        assertEquals("https://host", normalizeServerUrl("HTTPS://host"))
        assertEquals("http://host:8000", normalizeServerUrl("HTTP://HOST:8000"))
    }

    @Test
    fun `the default port for the scheme is dropped, any other port is kept`() {
        assertEquals("https://host", normalizeServerUrl("https://host:443"))
        assertEquals("http://host", normalizeServerUrl("http://host:80"))
        assertEquals("https://host:8443", normalizeServerUrl("https://host:8443/"))
    }

    @Test
    fun `trailing slashes are stripped`() {
        assertEquals("https://host", normalizeServerUrl("https://host/"))
        assertEquals("https://host:8443", normalizeServerUrl("https://host:8443///"))
    }

    @Test
    fun `non-http schemes and unparseable input are rejected`() {
        assertNull(normalizeServerUrl(""))
        assertNull(normalizeServerUrl("   "))
        assertNull(normalizeServerUrl("ftp://x"))
        assertNull(normalizeServerUrl("file:///etc/passwd"))
        assertNull(normalizeServerUrl("javascript:alert(1)"))
        assertNull(normalizeServerUrl("http://"))
        assertNull(normalizeServerUrl("https://"))
        assertNull(normalizeServerUrl("not a url"))
    }

    @Test
    fun `retrofitBaseUrl never returns something Retrofit would throw on`() {
        // The defensive half of the fix: even a garbage value written by an older
        // build must degrade to the placeholder rather than crash DI at startup.
        assertEquals(UNCONFIGURED_BASE_URL, retrofitBaseUrl("not a url"))
        assertEquals(UNCONFIGURED_BASE_URL, retrofitBaseUrl("javascript:alert(1)"))
        assertEquals(UNCONFIGURED_BASE_URL, retrofitBaseUrl("ftp://x"))
        // A scheme-less value — the exact string #149 was filed about.
        assertEquals("https://tandem.example.com/", retrofitBaseUrl("tandem.example.com"))
    }
}
