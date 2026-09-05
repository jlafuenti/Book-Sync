import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { TranscriptionSettingsSection, ABSSettingsSection, HardcoverSettingsSection, BackupSection, DetailedBreakdown } from './SystemPage'
import { roleMeets } from '../roles'

// TranscriptionSettingsSection/ABSSettingsSection talk to the API directly
// (no props), so mock the module they import from rather than mounting the
// whole page/router.
const {
    getSettingsMock, updateSettingsMock, testRemoteConnectionMock, generateKeyMock,
    testAbsConnectionMock, testHardcoverConnectionMock, enrichLibraryFromAbsMock,
    getBackupStatusMock, listBackupsMock, restoreBackupMock,
    createBackupMock, deleteBackupMock, downloadBackupMock,
    authRef,
} = vi.hoisted(() => ({
    getSettingsMock: vi.fn(),
    updateSettingsMock: vi.fn(),
    testRemoteConnectionMock: vi.fn(),
    generateKeyMock: vi.fn(),
    testAbsConnectionMock: vi.fn(),
    testHardcoverConnectionMock: vi.fn(),
    enrichLibraryFromAbsMock: vi.fn(),
    getBackupStatusMock: vi.fn(),
    listBackupsMock: vi.fn(),
    restoreBackupMock: vi.fn(),
    createBackupMock: vi.fn(),
    deleteBackupMock: vi.fn(),
    downloadBackupMock: vi.fn(),
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
        testHardcoverConnection: testHardcoverConnectionMock,
        enrichLibraryFromAbs: enrichLibraryFromAbsMock,
        getBackupStatus: getBackupStatusMock,
        listBackups: listBackupsMock,
        restoreBackup: restoreBackupMock,
        createBackup: createBackupMock,
        deleteBackup: deleteBackupMock,
        downloadBackup: downloadBackupMock,
    }
})

// The real comparison, not a copy of it: these mocks used to reimplement
// `(ROLE_HIERARCHY[role] || 0) >= (ROLE_HIERARCHY[min] || 0)`, which is the
// fail-open form, so a typo'd minimum behaved the same here as in the app
// and the suite stayed green either way (issue #359).
vi.mock('../contexts/AuthContext', () => ({
    useAuth: () => ({
        hasMinRole: (min) => roleMeets(authRef.role, min),
    }),
}))

function baseSettings(overrides = {}) {
    return {
        transcription_provider: 'remote_with_fallback',
        transcription_remote_url: 'http://192.168.1.50:9000',
        transcription_remote_key: '',
        transcription_remote_timeout: 86400,
        auto_transcribe_enabled: false,
        whisper_model: 'medium',
        transcription_language: '',
        abs_enabled: false,
        abs_url: '',
        abs_api_token: '',
        abs_audiobooks_prefix: '',
        hardcover_api_token: '',
        ...overrides,
    }
}

const KEY_PLACEHOLDER_TEXT = 'Shared secret for the Jetson server'
const ABS_TOKEN_PLACEHOLDER_TEXT = 'Paste your ABS API token'
const HC_TOKEN_PLACEHOLDER_TEXT = 'Paste your Hardcover API token'

beforeEach(() => {
    getSettingsMock.mockReset().mockResolvedValue(baseSettings())
    updateSettingsMock.mockReset().mockResolvedValue({})
    testRemoteConnectionMock.mockReset()
    generateKeyMock.mockReset()
    testAbsConnectionMock.mockReset()
    testHardcoverConnectionMock.mockReset()
    enrichLibraryFromAbsMock.mockReset()
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

    // Issue #248: the server honours this value now (it used to be clamped up
    // to 24 h), so the UI must state the real default and must not quietly
    // substitute a different one when the field is emptied.
    it('shows the 24h default for the remote timeout, not the old 7200s', async () => {
        render(<TranscriptionSettingsSection />)

        expect(await screen.findByText(/Default: 86400s \(24 h\)/)).toBeInTheDocument()
        expect(screen.getByText(/blocks for the whole job/)).toBeInTheDocument()
        expect(screen.queryByText(/7200/)).not.toBeInTheDocument()
    })

    it('falls back to 86400 when the timeout field is cleared', async () => {
        render(<TranscriptionSettingsSection />)
        const timeoutInput = await screen.findByRole('spinbutton')
        await waitFor(() => expect(timeoutInput).toHaveValue(86400))

        fireEvent.change(timeoutInput, { target: { value: '' } })
        fireEvent.click(screen.getByRole('button', { name: 'Save' }))

        await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith(
            expect.objectContaining({ transcription_remote_timeout: 86400 })
        ))
    })

    // Issue #246: without a pin, Whisper re-detects the language from the first
    // seconds of every chunk, so one chunk opening on music or a foreign
    // epigraph comes back as transliterated garbage for that whole span.
    it('loads the saved transcription language and defaults to auto-detect', async () => {
        getSettingsMock.mockResolvedValue(baseSettings({ transcription_language: 'de' }))
        render(<TranscriptionSettingsSection />)

        const select = await screen.findByLabelText('Transcription Language')
        await waitFor(() => expect(select).toHaveValue('de'))
        expect(screen.getByRole('option', { name: 'Auto-detect (per book)' })).toBeInTheDocument()
    })

    it('saves the chosen transcription language', async () => {
        render(<TranscriptionSettingsSection />)
        const select = await screen.findByLabelText('Transcription Language')
        await waitFor(() => expect(select).toHaveValue(''))

        fireEvent.change(select, { target: { value: 'es' } })
        fireEvent.click(screen.getByRole('button', { name: 'Save' }))

        await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith(
            expect.objectContaining({ transcription_language: 'es' })
        ))
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

    it('describes an idle-unloaded model as resting rather than broken', async () => {
        // The Jetson releases its weights when idle (#106) — "Not Loaded" would
        // read as a fault for what is the normal daytime state.
        testRemoteConnectionMock.mockResolvedValue({
            success: true, gpu_name: 'Orin', model_loaded: false, model_state: 'unloaded',
        })
        render(<TranscriptionSettingsSection />)
        await screen.findByPlaceholderText(KEY_PLACEHOLDER_TEXT)

        fireEvent.click(screen.getByRole('button', { name: 'Test Connection' }))

        expect(await screen.findByText(/loads on next job/)).toBeInTheDocument()
    })

    /* ── Off-hours scheduling window (issue #106) ── */

    it('hides the window fields until off-hours scheduling is enabled', async () => {
        render(<TranscriptionSettingsSection />)
        await screen.findByPlaceholderText(KEY_PLACEHOLDER_TEXT)

        expect(screen.queryByLabelText('Window')).not.toBeInTheDocument()

        fireEvent.click(screen.getByLabelText('Only transcribe during off-hours'))

        expect(await screen.findByLabelText('Window')).toBeInTheDocument()
        expect(screen.getByLabelText('Timezone')).toBeInTheDocument()
    })

    it('loads a saved window', async () => {
        getSettingsMock.mockResolvedValue(baseSettings({
            transcription_offhours_enabled: true,
            transcription_offhours_start: '22:00',
            transcription_offhours_end: '06:00',
            transcription_offhours_timezone: 'America/New_York',
        }))
        render(<TranscriptionSettingsSection />)

        const start = await screen.findByLabelText('Window')
        await waitFor(() => expect(start).toHaveValue('22:00'))
        expect(screen.getByLabelText('Timezone')).toHaveValue('America/New_York')
    })

    it('saves the window alongside the rest of the transcription settings', async () => {
        getSettingsMock.mockResolvedValue(baseSettings({
            transcription_offhours_enabled: true,
            transcription_offhours_start: '01:00',
            transcription_offhours_end: '07:00',
            transcription_offhours_timezone: 'UTC',
        }))
        render(<TranscriptionSettingsSection />)
        await screen.findByLabelText('Window')

        fireEvent.click(screen.getByRole('button', { name: 'Save' }))

        await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith(
            expect.objectContaining({
                transcription_offhours_enabled: true,
                transcription_offhours_start: '01:00',
                transcription_offhours_end: '07:00',
                transcription_offhours_timezone: 'UTC',
            })
        ))
    })

    it("explains that a job in flight pauses and resumes rather than restarting", async () => {
        getSettingsMock.mockResolvedValue(baseSettings({ transcription_offhours_enabled: true }))
        render(<TranscriptionSettingsSection />)

        expect(await screen.findByText(/resumes from that point next window/)).toBeInTheDocument()
    })

    it("surfaces the server's reason when it rejects an invalid window", async () => {
        getSettingsMock.mockResolvedValue(baseSettings({ transcription_offhours_enabled: true }))
        updateSettingsMock.mockRejectedValue(
            new Error('Off-hours window start and end must differ')
        )
        render(<TranscriptionSettingsSection />)
        await screen.findByLabelText('Window')

        fireEvent.click(screen.getByRole('button', { name: 'Save' }))

        expect(await screen.findByText(/start and end must differ/)).toBeInTheDocument()
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

    it('warns when re-enrich succeeds but some files could not be tagged', async () => {
        getSettingsMock.mockResolvedValue(baseSettings({ abs_enabled: true, abs_url: 'http://192.168.1.60:13378' }))
        enrichLibraryFromAbsMock.mockResolvedValue({
            message: 'Enriched 2 audiobook(s) from Audiobookshelf, but 1 file(s) could not be tagged',
            updated: 2,
            tag_write_failures: [
                { id: 1538, title: 'Antiagon Fire', error: "'utf-8' codec can't decode byte 0xc4 in position 27: invalid continuation byte" },
            ],
        })
        render(<ABSSettingsSection />)
        await screen.findByPlaceholderText(ABS_TOKEN_PLACEHOLDER_TEXT)

        fireEvent.click(screen.getByRole('button', { name: 'Re-enrich All from ABS' }))

        const alert = await screen.findByText(/could not be tagged/)
        expect(alert.closest('.alert')).toHaveClass('alert-warning')
        expect(alert.textContent).toContain('Antiagon Fire')
    })

    it('shows a plain success message when re-enrich has no tag-write failures', async () => {
        getSettingsMock.mockResolvedValue(baseSettings({ abs_enabled: true, abs_url: 'http://192.168.1.60:13378' }))
        enrichLibraryFromAbsMock.mockResolvedValue({
            message: 'Enriched 2 audiobook(s) from Audiobookshelf',
            updated: 2,
            tag_write_failures: [],
        })
        render(<ABSSettingsSection />)
        await screen.findByPlaceholderText(ABS_TOKEN_PLACEHOLDER_TEXT)

        fireEvent.click(screen.getByRole('button', { name: 'Re-enrich All from ABS' }))

        const alert = await screen.findByText('Enriched 2 audiobook(s) from Audiobookshelf')
        expect(alert.closest('.alert')).toHaveClass('alert-success')
    })
})

describe('HardcoverSettingsSection', () => {
    it('loads a previously-saved (masked) token as a password field', async () => {
        getSettingsMock.mockResolvedValue(baseSettings({ hardcover_api_token: '********' }))
        render(<HardcoverSettingsSection />)

        const tokenInput = await screen.findByPlaceholderText(HC_TOKEN_PLACEHOLDER_TEXT)
        await waitFor(() => expect(tokenInput).toHaveValue('********'))
        expect(tokenInput).toHaveAttribute('type', 'password')
    })

    it('saves the token via updateSettings', async () => {
        render(<HardcoverSettingsSection />)
        const tokenInput = await screen.findByPlaceholderText(HC_TOKEN_PLACEHOLDER_TEXT)
        fireEvent.change(tokenInput, { target: { value: 'my-new-token' } })

        fireEvent.click(screen.getByRole('button', { name: 'Save' }))

        await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith(
            { hardcover_api_token: 'my-new-token' }
        ))
        expect(await screen.findByText('Saved')).toBeInTheDocument()
    })

    it('sends an empty token to Test Connection when the field still shows the masked placeholder', async () => {
        getSettingsMock.mockResolvedValue(baseSettings({ hardcover_api_token: '********' }))
        testHardcoverConnectionMock.mockResolvedValue({ success: true, username: 'jesse' })
        render(<HardcoverSettingsSection />)
        const tokenInput = await screen.findByPlaceholderText(HC_TOKEN_PLACEHOLDER_TEXT)
        await waitFor(() => expect(tokenInput).toHaveValue('********'))

        fireEvent.click(screen.getByRole('button', { name: 'Test Connection' }))

        await waitFor(() => expect(testHardcoverConnectionMock).toHaveBeenCalledWith(''))
        expect(await screen.findByText(/Connected as jesse/)).toBeInTheDocument()
    })

    it('sends the real token to Test Connection when freshly typed', async () => {
        testHardcoverConnectionMock.mockResolvedValue({ success: true, username: 'jesse' })
        render(<HardcoverSettingsSection />)
        const tokenInput = await screen.findByPlaceholderText(HC_TOKEN_PLACEHOLDER_TEXT)
        fireEvent.change(tokenInput, { target: { value: 'freshly-typed' } })

        fireEvent.click(screen.getByRole('button', { name: 'Test Connection' }))

        await waitFor(() => expect(testHardcoverConnectionMock).toHaveBeenCalledWith('freshly-typed'))
    })

    it('reports an auth failure from Test Connection', async () => {
        testHardcoverConnectionMock.mockRejectedValue(new Error('Authentication failed — check your API token'))
        render(<HardcoverSettingsSection />)
        await screen.findByPlaceholderText(HC_TOKEN_PLACEHOLDER_TEXT)

        fireEvent.click(screen.getByRole('button', { name: 'Test Connection' }))

        expect(await screen.findByText(/Authentication failed/)).toBeInTheDocument()
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
                { id: '2026-07-13_090000-manual', db_file: 'booksync-db-2026-07-13_090000-manual.dump', db_size_bytes: 4096, has_covers: true, is_manual: true, label: 'before reorg', created_utc: '2026-07-13T09:00:00Z' },
                { id: '2026-07-13', db_file: 'booksync-db-2026-07-13.dump', db_size_bytes: 2048, has_covers: true, is_manual: false, label: null, created_utc: '2026-07-13T03:00:00Z' },
                { id: '2026-07-11', db_file: 'booksync-db-2026-07-11.dump', db_size_bytes: 1024, has_covers: false, is_manual: false, label: null, created_utc: '2026-07-11T03:00:00Z' },
            ],
        }
    }
    function backupSettings(overrides = {}) {
        return { backup_enabled: true, backup_hour: 3, backup_keep_daily: 14, backup_keep_monthly: 6, ...overrides }
    }

    beforeEach(() => {
        authRef.role = 'superadmin'
        getBackupStatusMock.mockReset().mockResolvedValue(recentStatus())
        listBackupsMock.mockReset().mockResolvedValue(backupList())
        getSettingsMock.mockReset().mockResolvedValue(backupSettings())
        updateSettingsMock.mockReset().mockResolvedValue({})
        restoreBackupMock.mockReset().mockResolvedValue({ restored: true, backup_id: '2026-07-13', covers_restored: true })
        createBackupMock.mockReset().mockResolvedValue({ id: '2026-07-14_120000-manual', is_manual: true })
        deleteBackupMock.mockReset().mockResolvedValue({ deleted: true })
        downloadBackupMock.mockReset().mockResolvedValue(undefined)
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

    it('lists backups with covers, manual badge and label', async () => {
        render(<BackupSection />)
        expect(await screen.findByText('2026-07-13_090000-manual')).toBeInTheDocument()
        expect(screen.getByText('2026-07-11')).toBeInTheDocument()
        expect(screen.getByText(/before reorg/)).toBeInTheDocument()
        expect(screen.getByText(/Manual/)).toBeInTheDocument()
        expect(screen.getAllByText(/\+ covers/)).toHaveLength(2)  // the two has_covers rows
    })

    it('loads retention/schedule config and saves it', async () => {
        render(<BackupSection />)
        const keepDaily = await screen.findByLabelText('Keep daily')
        expect(keepDaily).toHaveValue(14)

        fireEvent.change(keepDaily, { target: { value: '7' } })
        fireEvent.click(screen.getByRole('button', { name: 'Save' }))

        await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith(
            expect.objectContaining({ backup_keep_daily: 7, backup_hour: 3, backup_keep_monthly: 6, backup_enabled: true })
        ))
        expect(await screen.findByText('Saved')).toBeInTheDocument()
    })

    it('creates a manual backup with a label (superadmin)', async () => {
        render(<BackupSection />)
        const labelInput = await screen.findByPlaceholderText(/Optional label/i)
        fireEvent.change(labelInput, { target: { value: 'pre upgrade' } })
        fireEvent.click(screen.getByRole('button', { name: /Create backup/i }))

        await waitFor(() => expect(createBackupMock).toHaveBeenCalledWith('pre upgrade'))
        // List is refreshed after a successful create.
        await waitFor(() => expect(listBackupsMock.mock.calls.length).toBeGreaterThan(1))
    })

    it('restores a row after typed confirmation (superadmin)', async () => {
        render(<BackupSection />)
        await screen.findByText('2026-07-11')

        fireEvent.click(screen.getByRole('button', { name: 'Restore 2026-07-11' }))
        const confirmInput = await screen.findByPlaceholderText(/RESTORE/i)
        const confirmBtn = screen.getByRole('button', { name: /Confirm restore/i })
        expect(confirmBtn).toBeDisabled()
        fireEvent.change(confirmInput, { target: { value: 'RESTORE' } })
        fireEvent.click(confirmBtn)

        await waitFor(() => expect(restoreBackupMock).toHaveBeenCalledWith('2026-07-11'))
        expect(await screen.findByText(/Restored/i)).toBeInTheDocument()
    })

    it('deletes a row after confirmation (superadmin)', async () => {
        render(<BackupSection />)
        await screen.findByText('2026-07-11')

        fireEvent.click(screen.getByRole('button', { name: 'Delete 2026-07-11' }))
        fireEvent.click(await screen.findByRole('button', { name: /Confirm delete/i }))

        await waitFor(() => expect(deleteBackupMock).toHaveBeenCalledWith('2026-07-11'))
        await waitFor(() => expect(listBackupsMock.mock.calls.length).toBeGreaterThan(1))
    })

    it('downloads a row (superadmin)', async () => {
        render(<BackupSection />)
        await screen.findByText('2026-07-11')

        fireEvent.click(screen.getByRole('button', { name: 'Download 2026-07-11' }))
        await waitFor(() => expect(downloadBackupMock).toHaveBeenCalledWith('2026-07-11'))
    })

    it('surfaces a restore failure', async () => {
        restoreBackupMock.mockRejectedValue(new Error('pg_restore exploded'))
        render(<BackupSection />)
        await screen.findByText('2026-07-11')

        fireEvent.click(screen.getByRole('button', { name: 'Restore 2026-07-11' }))
        fireEvent.change(await screen.findByPlaceholderText(/RESTORE/i), { target: { value: 'RESTORE' } })
        fireEvent.click(screen.getByRole('button', { name: /Confirm restore/i }))

        expect(await screen.findByText(/pg_restore exploded/)).toBeInTheDocument()
    })

    it('hides create/restore/delete/download from non-superadmins but keeps config', async () => {
        authRef.role = 'admin'
        render(<BackupSection />)
        await screen.findByText('2026-07-11')

        expect(screen.queryByRole('button', { name: /Create backup/i })).not.toBeInTheDocument()
        expect(screen.queryByRole('button', { name: /^Restore / })).not.toBeInTheDocument()
        expect(screen.queryByRole('button', { name: /^Delete / })).not.toBeInTheDocument()
        expect(screen.queryByRole('button', { name: /^Download / })).not.toBeInTheDocument()
        // Admins can still edit retention/schedule.
        expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument()
    })
})

// Issue #271: five columns of disk figures do not fit a 375px phone, and the
// card around them clips overflow, so the last columns were unreachable.
describe('DetailedBreakdown mobile layout', () => {
    const STATS = {
        ebook_used_human: '120 GB', ebook_total_human: '500 GB', ebook_free_human: '380 GB',
        ebook_used_bytes: 120, ebook_total_bytes: 500,
        audiobook_used_human: '40 GB', audiobook_total_human: '500 GB', audiobook_free_human: '460 GB',
        audiobook_used_bytes: 40, audiobook_total_bytes: 500,
        app_data_used_human: '2 GB', app_data_total_human: '50 GB', app_data_free_human: '48 GB',
        app_data_used_bytes: 2, app_data_total_bytes: 50,
    }

    it('renders the storage table inside a horizontal-scroll wrapper', () => {
        render(<DetailedBreakdown stats={STATS} />)

        expect(screen.getByRole('table').closest('.table-wrapper')).not.toBeNull()
    })
})
