import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { TranscriptionSettingsSection, ABSSettingsSection, BackupSection } from './SystemPage'

// TranscriptionSettingsSection/ABSSettingsSection talk to the API directly
// (no props), so mock the module they import from rather than mounting the
// whole page/router.
const {
    getSettingsMock, updateSettingsMock, testRemoteConnectionMock, generateKeyMock,
    testAbsConnectionMock, getBackupStatusMock, listBackupsMock, restoreBackupMock,
    authRef,
} = vi.hoisted(() => ({
    getSettingsMock: vi.fn(),
    updateSettingsMock: vi.fn(),
    testRemoteConnectionMock: vi.fn(),
    generateKeyMock: vi.fn(),
    testAbsConnectionMock: vi.fn(),
    getBackupStatusMock: vi.fn(),
    listBackupsMock: vi.fn(),
    restoreBackupMock: vi.fn(),
    authRef: { role: 'superadmin' },
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        getSettings: getSettingsMock,
        updateSettings: updateSettingsMock,
        testRemoteConnection: testRemoteConnectionMock,
        generateTranscriptionRemoteKey: generateKeyMock,
        testAbsConnection: testAbsConnectionMock,
        getBackupStatus: getBackupStatusMock,
        listBackups: listBackupsMock,
        restoreBackup: restoreBackupMock,
    }
})

const ROLE_HIERARCHY = { superadmin: 4, admin: 3, editor: 2, user: 1 }
vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({
        hasMinRole: (min) => (ROLE_HIERARCHY[authRef.role] || 0) >= (ROLE_HIERARCHY[min] || 0),
    }),
}))

function baseSettings(overrides = {}) {
    return {
        transcription_provider: 'remote_with_fallback',
        transcription_remote_url: 'http://192.168.1.50:9000',
        transcription_remote_key: '',
        transcription_remote_timeout: 7200,
        auto_transcribe_enabled: false,
        whisper_model: 'medium',
        abs_enabled: false,
        abs_url: '',
        abs_api_token: '',
        abs_audiobooks_prefix: '',
        ...overrides,
    }
}

const KEY_PLACEHOLDER_TEXT = 'Shared secret for the Jetson server'
const ABS_TOKEN_PLACEHOLDER_TEXT = 'Paste your ABS API token'

beforeEach(() => {
    getSettingsMock.mockReset().mockResolvedValue(baseSettings())
    updateSettingsMock.mockReset().mockResolvedValue({})
    testRemoteConnectionMock.mockReset()
    generateKeyMock.mockReset()
    testAbsConnectionMock.mockReset()
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } })
})

describe('TranscriptionSettingsSection', () => {
    it('loads a previously-saved (masked) key on mount as a password field', async () => {
        getSettingsMock.mockResolvedValue(baseSettings({ transcription_remote_key: '********' }))
        render(<TranscriptionSettingsSection />)

        const keyInput = await screen.findByPlaceholderText(KEY_PLACEHOLDER_TEXT)
        await waitFor(() => expect(keyInput).toHaveValue('********'))
        expect(keyInput).toHaveAttribute('type', 'password')
        expect(screen.getByText(/Required by the Jetson server/)).toBeInTheDocument()
    })

    it('saves settings including whatever key is currently in the field', async () => {
        render(<TranscriptionSettingsSection />)
        await screen.findByPlaceholderText(KEY_PLACEHOLDER_TEXT)

        fireEvent.click(screen.getByRole('button', { name: 'Save' }))

        await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith(
            expect.objectContaining({ transcription_remote_key: '' })
        ))
        expect(await screen.findByText('Saved')).toBeInTheDocument()
    })

    it('shows a generated key in the clear with a Copy button and Jetson-restart hint', async () => {
        generateKeyMock.mockResolvedValue({ key: 'brand-new-key-123' })
        render(<TranscriptionSettingsSection />)
        await screen.findByPlaceholderText(KEY_PLACEHOLDER_TEXT)

        fireEvent.click(screen.getByRole('button', { name: 'Generate Key' }))

        const keyInput = await screen.findByPlaceholderText(KEY_PLACEHOLDER_TEXT)
        await waitFor(() => expect(keyInput).toHaveValue('brand-new-key-123'))
        expect(keyInput).toHaveAttribute('type', 'text')
        expect(screen.getByRole('button', { name: 'Copy' })).toBeInTheDocument()
        expect(screen.getByText(/won't be shown again/)).toBeInTheDocument()
    })

    it('copies the generated key to the clipboard and flips the button to "Copied!"', async () => {
        generateKeyMock.mockResolvedValue({ key: 'brand-new-key-123' })
        render(<TranscriptionSettingsSection />)
        await screen.findByPlaceholderText(KEY_PLACEHOLDER_TEXT)
        fireEvent.click(screen.getByRole('button', { name: 'Generate Key' }))
        await screen.findByRole('button', { name: 'Copy' })

        fireEvent.click(screen.getByRole('button', { name: 'Copy' }))

        await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith('brand-new-key-123'))
        expect(await screen.findByRole('button', { name: 'Copied!' })).toBeInTheDocument()
    })

    it('reports an error and offers manual copy if the clipboard API rejects', async () => {
        generateKeyMock.mockResolvedValue({ key: 'brand-new-key-123' })
        vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
        render(<TranscriptionSettingsSection />)
        await screen.findByPlaceholderText(KEY_PLACEHOLDER_TEXT)
        fireEvent.click(screen.getByRole('button', { name: 'Generate Key' }))
        await screen.findByRole('button', { name: 'Copy' })

        fireEvent.click(screen.getByRole('button', { name: 'Copy' }))

        expect(await screen.findByText(/select the text and copy manually/)).toBeInTheDocument()
    })

    it('reports a failure if key generation itself fails', async () => {
        generateKeyMock.mockRejectedValue(new Error('server exploded'))
        render(<TranscriptionSettingsSection />)
        await screen.findByPlaceholderText(KEY_PLACEHOLDER_TEXT)

        fireEvent.click(screen.getByRole('button', { name: 'Generate Key' }))

        expect(await screen.findByText(/Failed to generate key: server exploded/)).toBeInTheDocument()
    })

    it('sends an empty key to Test Connection when the field still shows the masked placeholder', async () => {
        getSettingsMock.mockResolvedValue(baseSettings({ transcription_remote_key: '********' }))
        testRemoteConnectionMock.mockResolvedValue({ success: true, gpu_name: 'Orin', model_loaded: true })
        render(<TranscriptionSettingsSection />)
        const keyInput = await screen.findByPlaceholderText(KEY_PLACEHOLDER_TEXT)
        await waitFor(() => expect(keyInput).toHaveValue('********'))

        fireEvent.click(screen.getByRole('button', { name: 'Test Connection' }))

        await waitFor(() => expect(testRemoteConnectionMock).toHaveBeenCalledWith(
            'http://192.168.1.50:9000', ''
        ))
        expect(await screen.findByText(/Connected! GPU: Orin/)).toBeInTheDocument()
    })

    it('sends the real key to Test Connection when one was freshly typed/generated', async () => {
        generateKeyMock.mockResolvedValue({ key: 'brand-new-key-123' })
        testRemoteConnectionMock.mockResolvedValue({ success: false })
        render(<TranscriptionSettingsSection />)
        await screen.findByPlaceholderText(KEY_PLACEHOLDER_TEXT)
        fireEvent.click(screen.getByRole('button', { name: 'Generate Key' }))
        await screen.findByRole('button', { name: 'Copy' })

        fireEvent.click(screen.getByRole('button', { name: 'Test Connection' }))

        await waitFor(() => expect(testRemoteConnectionMock).toHaveBeenCalledWith(
            'http://192.168.1.50:9000', 'brand-new-key-123'
        ))
        expect(await screen.findByText(/Server responded but not healthy/)).toBeInTheDocument()
    })

    it('reports a connection error from Test Connection', async () => {
        testRemoteConnectionMock.mockRejectedValue(new Error('Connection failed: timeout'))
        render(<TranscriptionSettingsSection />)
        await screen.findByPlaceholderText(KEY_PLACEHOLDER_TEXT)

        fireEvent.click(screen.getByRole('button', { name: 'Test Connection' }))

        expect(await screen.findByText(/Connection failed: timeout/)).toBeInTheDocument()
    })
})

describe('ABSSettingsSection', () => {
    it('sends an empty token to Test Connection when the field still shows the masked placeholder', async () => {
        getSettingsMock.mockResolvedValue(baseSettings({
            abs_enabled: true, abs_url: 'http://192.168.1.60:13378', abs_api_token: '********',
        }))
        testAbsConnectionMock.mockResolvedValue({ success: true, book_libraries: ['Audiobooks'] })
        render(<ABSSettingsSection />)
        const tokenInput = await screen.findByPlaceholderText(ABS_TOKEN_PLACEHOLDER_TEXT)
        await waitFor(() => expect(tokenInput).toHaveValue('********'))

        fireEvent.click(screen.getByRole('button', { name: 'Test Connection' }))

        await waitFor(() => expect(testAbsConnectionMock).toHaveBeenCalledWith(
            'http://192.168.1.60:13378', ''
        ))
        expect(await screen.findByText(/Connected! Libraries: Audiobooks/)).toBeInTheDocument()
    })

    it('sends the real token to Test Connection when one was freshly typed', async () => {
        getSettingsMock.mockResolvedValue(baseSettings({ abs_enabled: true, abs_url: 'http://192.168.1.60:13378' }))
        testAbsConnectionMock.mockResolvedValue({ success: true, book_libraries: [] })
        render(<ABSSettingsSection />)
        const tokenInput = await screen.findByPlaceholderText(ABS_TOKEN_PLACEHOLDER_TEXT)
        fireEvent.change(tokenInput, { target: { value: 'freshly-typed-token' } })

        fireEvent.click(screen.getByRole('button', { name: 'Test Connection' }))

        await waitFor(() => expect(testAbsConnectionMock).toHaveBeenCalledWith(
            'http://192.168.1.60:13378', 'freshly-typed-token'
        ))
    })

    it('requires a URL before testing', async () => {
        getSettingsMock.mockResolvedValue(baseSettings({ abs_enabled: true }))
        render(<ABSSettingsSection />)
        await screen.findByPlaceholderText(ABS_TOKEN_PLACEHOLDER_TEXT)

        fireEvent.click(screen.getByRole('button', { name: 'Test Connection' }))

        expect(await screen.findByText('Enter URL first')).toBeInTheDocument()
        expect(testAbsConnectionMock).not.toHaveBeenCalled()
    })

    it('reports a connection error from Test Connection', async () => {
        getSettingsMock.mockResolvedValue(baseSettings({ abs_enabled: true, abs_url: 'http://192.168.1.60:13378' }))
        testAbsConnectionMock.mockRejectedValue(new Error('Connection failed: timeout'))
        render(<ABSSettingsSection />)
        await screen.findByPlaceholderText(ABS_TOKEN_PLACEHOLDER_TEXT)

        fireEvent.click(screen.getByRole('button', { name: 'Test Connection' }))

        expect(await screen.findByText(/Connection failed: timeout/)).toBeInTheDocument()
    })
})

describe('BackupSection', () => {
    function recentStatus(overrides = {}) {
        return {
            configured: true,
            location: '/backups',
            last_backup_utc: '2026-07-13T03:00:11Z',
            age_seconds: 120,
            stale: false,
            latest_db_file: 'booksync-db-2026-07-13.dump',
            latest_db_size_bytes: 2048,
            ...overrides,
        }
    }
    function backupList() {
        return {
            location: '/backups',
            items: [
                { id: '2026-07-13', db_file: 'booksync-db-2026-07-13.dump', db_size_bytes: 2048, has_covers: true },
                { id: '2026-07-11', db_file: 'booksync-db-2026-07-11.dump', db_size_bytes: 1024, has_covers: false },
            ],
        }
    }

    beforeEach(() => {
        authRef.role = 'superadmin'
        getBackupStatusMock.mockReset().mockResolvedValue(recentStatus())
        listBackupsMock.mockReset().mockResolvedValue(backupList())
        restoreBackupMock.mockReset().mockResolvedValue({ restored: true, backup_id: '2026-07-13', covers_restored: true })
    })

    it('shows the backup location and last successful run', async () => {
        render(<BackupSection />)
        expect(await screen.findByText('/backups')).toBeInTheDocument()
        expect(screen.getByText(/2026-07-13T03:00:11Z/)).toBeInTheDocument()
    })

    it('flags a stale/overdue backup', async () => {
        getBackupStatusMock.mockResolvedValue(recentStatus({ stale: true }))
        render(<BackupSection />)
        expect(await screen.findByText(/Overdue/i)).toBeInTheDocument()
    })

    it('reports when no backups have run yet', async () => {
        getBackupStatusMock.mockResolvedValue(recentStatus({ configured: false, stale: true, last_backup_utc: null, age_seconds: null, latest_db_file: null, latest_db_size_bytes: null }))
        listBackupsMock.mockResolvedValue({ location: '/backups', items: [] })
        render(<BackupSection />)
        expect(await screen.findByText(/No backups/i)).toBeInTheDocument()
    })

    it('lists available backups and marks which have covers', async () => {
        render(<BackupSection />)
        expect(await screen.findByText('2026-07-13')).toBeInTheDocument()
        expect(screen.getByText('2026-07-11')).toBeInTheDocument()
        // Only the has_covers backup shows the "+ covers" indicator.
        expect(screen.getAllByText(/\+ covers/)).toHaveLength(1)
    })

    it('hides the Restore control from non-superadmins', async () => {
        authRef.role = 'admin'
        render(<BackupSection />)
        await screen.findByText('2026-07-13')
        expect(screen.queryByRole('button', { name: /Restore/i })).not.toBeInTheDocument()
    })

    it('restores the selected backup after typed confirmation (superadmin)', async () => {
        render(<BackupSection />)
        await screen.findByText('2026-07-13')

        fireEvent.click(screen.getByRole('button', { name: /^Restore/i }))

        const confirmInput = await screen.findByPlaceholderText(/RESTORE/i)
        // Confirm is disabled until the exact word is typed.
        const confirmBtn = screen.getByRole('button', { name: /Confirm restore/i })
        expect(confirmBtn).toBeDisabled()

        fireEvent.change(confirmInput, { target: { value: 'RESTORE' } })
        expect(confirmBtn).toBeEnabled()
        fireEvent.click(confirmBtn)

        await waitFor(() => expect(restoreBackupMock).toHaveBeenCalledWith('2026-07-13'))
        expect(await screen.findByText(/Restored/i)).toBeInTheDocument()
    })

    it('surfaces a restore failure', async () => {
        restoreBackupMock.mockRejectedValue(new Error('pg_restore exploded'))
        render(<BackupSection />)
        await screen.findByText('2026-07-13')

        fireEvent.click(screen.getByRole('button', { name: /^Restore/i }))
        fireEvent.change(await screen.findByPlaceholderText(/RESTORE/i), { target: { value: 'RESTORE' } })
        fireEvent.click(screen.getByRole('button', { name: /Confirm restore/i }))

        expect(await screen.findByText(/pg_restore exploded/)).toBeInTheDocument()
    })
})
