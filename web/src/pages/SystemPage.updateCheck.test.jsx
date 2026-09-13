import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SystemPage from './SystemPage'
import { roleMeets } from '../roles'

/**
 * The update check on the System page (issue #463).
 *
 * `UpdateCheckBanner.test.jsx` covers what each state says. This covers the
 * wiring: the status is read as a side fetch that cannot take the dashboard
 * down with it, only an admin asks, and the prompt's two answers save exactly
 * the settings they mean.
 */

const {
    diskMock, ebooksMock, audiobooksMock, pairsMock, queueMock, unsupportedMock,
    settingsMock, updateSettingsMock, calibreMock, getUsersMock, updateStatusMock, authRef,
} = vi.hoisted(() => ({
    diskMock: vi.fn(),
    ebooksMock: vi.fn(),
    audiobooksMock: vi.fn(),
    pairsMock: vi.fn(),
    queueMock: vi.fn(),
    unsupportedMock: vi.fn(),
    settingsMock: vi.fn(),
    updateSettingsMock: vi.fn(),
    calibreMock: vi.fn(),
    getUsersMock: vi.fn(),
    updateStatusMock: vi.fn(),
    authRef: { role: 'admin' },
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        getDiskUsage: diskMock,
        getEbooks: ebooksMock,
        getAudiobooks: audiobooksMock,
        getPairs: pairsMock,
        getTranscriptionQueue: queueMock,
        getUnsupportedFiles: unsupportedMock,
        getSettings: settingsMock,
        updateSettings: updateSettingsMock,
        getCalibreStatus: calibreMock,
        getUsers: getUsersMock,
        getUpdateStatus: updateStatusMock,
    }
})

vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({
        hasMinRole: (min) => roleMeets(authRef.role, min),
    }),
}))

vi.mock('./UserManagementPage', () => ({
    UserManagementSection: () => <div>users-stub</div>,
}))

const status = (overrides) => ({
    enabled: false,
    prompted: false,
    status: 'unknown',
    reason: 'disabled',
    running_version: '0.1.0',
    latest_version: null,
    release_url: null,
    checked_at: null,
    ...overrides,
})

beforeEach(() => {
    authRef.role = 'admin'
    diskMock.mockReset().mockResolvedValue({ disks: [] })
    ebooksMock.mockReset().mockResolvedValue([])
    audiobooksMock.mockReset().mockResolvedValue([])
    pairsMock.mockReset().mockResolvedValue([])
    queueMock.mockReset().mockResolvedValue([])
    unsupportedMock.mockReset().mockResolvedValue([])
    settingsMock.mockReset().mockResolvedValue({})
    updateSettingsMock.mockReset().mockResolvedValue({})
    calibreMock.mockReset().mockResolvedValue({ available: false })
    getUsersMock.mockReset().mockResolvedValue([])
    updateStatusMock.mockReset().mockResolvedValue(status())
})

const renderPage = () => render(<MemoryRouter><SystemPage tab="status" /></MemoryRouter>)

describe('update check on the System page (issue #463)', () => {
    it('asks a fresh install whether to check', async () => {
        renderPage()
        expect(await screen.findByText(/check for updates automatically/i)).toBeInTheDocument()
    })

    it('Enable turns the check on and records the answer', async () => {
        renderPage()
        fireEvent.click(await screen.findByRole('button', { name: /^enable$/i }))

        await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({
            update_check_enabled: true,
            update_check_prompted: true,
        }))
    })

    it('No thanks records the answer and leaves the check off', async () => {
        renderPage()
        fireEvent.click(await screen.findByRole('button', { name: /no thanks/i }))

        await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({
            update_check_prompted: true,
        }))
        expect(updateSettingsMock.mock.calls[0][0]).not.toHaveProperty('update_check_enabled')
    })

    it('re-reads the status after answering, so the prompt goes away', async () => {
        updateStatusMock
            .mockResolvedValueOnce(status())
            .mockResolvedValue(status({ prompted: true }))
        renderPage()

        fireEvent.click(await screen.findByRole('button', { name: /no thanks/i }))

        await waitFor(() =>
            expect(screen.queryByText(/check for updates automatically/i)).toBeNull())
    })

    it('shows the banner when a newer release is out', async () => {
        updateStatusMock.mockResolvedValue(status({
            enabled: true, prompted: true, status: 'available', reason: null,
            latest_version: '0.2.0',
        }))
        renderPage()

        expect(await screen.findByRole('status')).toHaveTextContent('0.2.0')
    })

    it('does not ask from an editor session, which would only 403', async () => {
        authRef.role = 'editor'
        renderPage()

        await new Promise(r => setTimeout(r, 0))
        expect(updateStatusMock).not.toHaveBeenCalled()
    })

    it('leaves the dashboard intact when the status cannot be read', async () => {
        updateStatusMock.mockRejectedValue(new Error('boom'))
        renderPage()

        expect(await screen.findByText('Total Books')).toBeInTheDocument()
        expect(screen.queryByText(/check for updates automatically/i)).toBeNull()
    })
})
