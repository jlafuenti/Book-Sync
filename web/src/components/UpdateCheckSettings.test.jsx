import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import UpdateCheckSettings from './UpdateCheckSettings'

/**
 * The durable switch for the update check (issue #463).
 *
 * The System page asks once. An admin who answered "No thanks" needs a way to
 * change their mind later, and one who enabled it needs a way to stop the
 * server contacting GitHub — without the prompt coming back either way, which
 * is why every save here also records the question as answered.
 */

const { getSettingsMock, updateSettingsMock } = vi.hoisted(() => ({
    getSettingsMock: vi.fn(),
    updateSettingsMock: vi.fn(),
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return { ...actual, getSettings: getSettingsMock, updateSettings: updateSettingsMock }
})

beforeEach(() => {
    getSettingsMock.mockReset().mockResolvedValue({ update_check_enabled: false })
    updateSettingsMock.mockReset().mockResolvedValue({})
})

const checkbox = () => screen.findByRole('checkbox', { name: /check github for new releases/i })

describe('update check setting', () => {
    it('shows the stored value', async () => {
        getSettingsMock.mockResolvedValue({ update_check_enabled: true })
        render(<UpdateCheckSettings />)

        await waitFor(async () => expect(await checkbox()).toBeChecked())
    })

    it('turning it on enables the check and marks the prompt answered', async () => {
        render(<UpdateCheckSettings />)
        fireEvent.click(await checkbox())

        await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({
            update_check_enabled: true,
            update_check_prompted: true,
        }))
    })

    it('turning it off stops the check and does not bring the prompt back', async () => {
        getSettingsMock.mockResolvedValue({ update_check_enabled: true })
        render(<UpdateCheckSettings />)
        const box = await checkbox()
        await waitFor(() => expect(box).toBeChecked())

        fireEvent.click(box)

        await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({
            update_check_enabled: false,
            update_check_prompted: true,
        }))
    })

    it('says what enabling it discloses', async () => {
        render(<UpdateCheckSettings />)
        await checkbox()
        expect(screen.getByText(/sees this server.s address/i)).toBeInTheDocument()
    })

    it('puts the switch back and says so when the save fails', async () => {
        updateSettingsMock.mockRejectedValue(new Error('Failed to update settings'))
        render(<UpdateCheckSettings />)
        const box = await checkbox()

        fireEvent.click(box)

        expect(await screen.findByText(/failed to update settings/i)).toBeInTheDocument()
        expect(box).not.toBeChecked()
    })
})
