package com.booksync.data.repository

import com.booksync.data.remote.UserScopeProvider
import io.mockk.every
import io.mockk.mockk

/**
 * A resolved account for repository tests (issue #314).
 *
 * Every cache read and write is scoped now, so a relaxed mock — whose `currentKey`
 * is null — puts the repository in its "no resolvable account" state, where reads
 * match nothing and the queue drain refuses to run. That is correct behaviour but
 * makes almost every existing test assert on an empty database, so tests that are
 * not *about* scoping declare a signed-in account explicitly.
 */
internal const val TEST_SCOPE = "https://test.example.com|1"

internal fun testScopeProvider(scope: String? = TEST_SCOPE): UserScopeProvider =
    mockk<UserScopeProvider>(relaxed = true).also { every { it.currentKey } returns scope }
