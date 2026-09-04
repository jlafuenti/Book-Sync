import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const { logoutAllMock } = vi.hoisted(() => ({
    logoutAllMock: vi.fn().mockResolvedValue(undefined),
}))
vi.mock('../api', () => ({ logoutAll: logoutAllMock }))

import SignOutEverywhere from './SignOutEverywhere'

describe('SignOutEverywhere (issue #250)', () => {
    let confirmSpy

    beforeEach(() => {
        logoutAllMock.mockReset().mockResolvedValue(undefined)
        confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    })

    afterEach(() => {
        confirmSpy.mockRestore()
    })

    it('asks before doing anything', () => {
        confirmSpy.mockReturnValue(false)
        const onSignedOut = vi.fn()

        render(<SignOutEverywhere onSignedOut={onSignedOut} />)
        fireEvent.click(screen.getByTestId('sign-out-everywhere'))

        expect(confirmSpy).toHaveBeenCalledTimes(1)
        expect(logoutAllMock).not.toHaveBeenCalled()
        expect(onSignedOut).not.toHaveBeenCalled()
    })

    it('says what it does to a device that has not synced yet', () => {
        // The revoke is instant and account-wide; a phone holding unpushed
        // positions cannot send them once its session is gone
        // (docs/position-sync-contract.md). Saying so is the whole point of
        // the confirm.
        render(<SignOutEverywhere onSignedOut={vi.fn()} />)
        fireEvent.click(screen.getByTestId('sign-out-everywhere'))

        const message = confirmSpy.mock.calls[0][0]
        expect(message).toMatch(/every device/i)
        expect(message).toMatch(/not synced/i)
    })

    it('revokes on the server and then ends the local session', async () => {
        const onSignedOut = vi.fn()

        render(<SignOutEverywhere onSignedOut={onSignedOut} />)
        fireEvent.click(screen.getByTestId('sign-out-everywhere'))

        await waitFor(() => expect(onSignedOut).toHaveBeenCalledTimes(1))
        expect(logoutAllMock).toHaveBeenCalledTimes(1)
        // Ordering: the bearer token has to still be there when the revoke goes
        // out, so nothing local may be torn down first.
        expect(logoutAllMock.mock.invocationCallOrder[0])
            .toBeLessThan(onSignedOut.mock.invocationCallOrder[0])
    })

    it('ends the local session even when the revoke rejects', async () => {
        logoutAllMock.mockRejectedValue(new Error('offline'))
        const onSignedOut = vi.fn()

        render(<SignOutEverywhere onSignedOut={onSignedOut} />)
        fireEvent.click(screen.getByTestId('sign-out-everywhere'))

        await waitFor(() => expect(onSignedOut).toHaveBeenCalledTimes(1))
    })

    it('cannot be fired twice while the first call is in flight', async () => {
        let release
        logoutAllMock.mockImplementation(() => new Promise(r => { release = r }))

        render(<SignOutEverywhere onSignedOut={vi.fn()} />)
        const button = screen.getByTestId('sign-out-everywhere')
        fireEvent.click(button)
        await waitFor(() => expect(button.disabled).toBe(true))
        fireEvent.click(button)

        expect(logoutAllMock).toHaveBeenCalledTimes(1)
        release()
    })
})
