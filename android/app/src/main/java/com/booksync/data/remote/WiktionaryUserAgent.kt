package com.booksync.data.remote

/**
 * The `User-Agent` Wikimedia requires from API clients.
 *
 * Wikimedia's robot policy refuses a request whose agent string is a bare
 * library default: `okhttp/4.12.0` gets **403** with "Please set a user-agent
 * and respect our robot policy" (verified against the live endpoint, issue
 * #608 follow-up). The policy asks for something that identifies the
 * application and gives them a way to make contact, so this names the app,
 * its version, the project site and the public support address — nothing
 * about the device or the person using it.
 */
fun wiktionaryUserAgent(versionName: String): String =
    "Tandem/$versionName (https://tandembook.com/; support@tandembook.com)"
