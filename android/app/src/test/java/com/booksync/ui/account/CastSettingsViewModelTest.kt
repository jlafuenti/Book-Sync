package com.booksync.ui.account

import com.booksync.cast.CastPortManager
import com.booksync.cast.CastSessionMonitor
import com.booksync.player.CastServerAddress
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** The Account screen's cast-port field (issue #931). */
@OptIn(ExperimentalCoroutinesApi::class)
class CastSettingsViewModelTest {

    private val dispatcher = StandardTestDispatcher()
    private val savedPort = MutableStateFlow(8484)
    private val address = MutableStateFlow<CastServerAddress?>(null)

    private val portManager = mockk<CastPortManager>(relaxed = true).also {
        every { it.port } returns savedPort
        every { it.currentPort } returns 8484
        coEvery { it.setPort(any()) } returns true
    }
    private val monitor = mockk<CastSessionMonitor>(relaxed = true).also {
        every { it.serverAddress } returns address
    }

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun vm() = CastSettingsViewModel(portManager, monitor, mockk(relaxed = true))

    @Test
    fun `a valid port is saved and clears any error`() = runTest(dispatcher.scheduler) {
        val vm = vm()
        vm.commitPort("abc")
        assertNotNull(vm.portError.value)

        assertTrue(vm.commitPort(" 9000 "))
        advanceUntilIdle()

        assertNull(vm.portError.value)
        coVerify { portManager.setPort(9000) }
    }

    @Test
    fun `an invalid entry is refused with a message and nothing is saved`() = runTest(dispatcher.scheduler) {
        val vm = vm()
        for (bad in listOf("", "abc", "80", "70000", "8484.5")) {
            assertFalse(bad, vm.commitPort(bad))
        }
        advanceUntilIdle()

        assertEquals("Enter a port from 1024 to 65535.", vm.portError.value)
        coVerify(exactly = 0) { portManager.setPort(any()) }
    }

    @Test
    fun `typing again clears the error`() = runTest(dispatcher.scheduler) {
        val vm = vm()
        vm.commitPort("1")
        vm.clearPortError()
        assertNull(vm.portError.value)
    }

    @Test
    fun `the saved port and the live server address are passed through`() = runTest(dispatcher.scheduler) {
        val vm = vm()
        assertEquals(8484, vm.port.value)
        assertNull(vm.serverAddress.value)

        val live = CastServerAddress("192.0.2.2", 41000, "token", usingFallbackPort = true)
        address.value = live

        assertEquals(live, vm.serverAddress.value)
    }
}
