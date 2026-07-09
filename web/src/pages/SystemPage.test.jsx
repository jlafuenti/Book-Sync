import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { TranscriptionSettingsSection } from './SystemPage'

// TranscriptionSettingsSection talks to the API directly (no props), so mock
// the module it imports from rather than mounting the whole page/router.
const {
    getSettingsMock, updateSettingsMock, testRemoteConnectionMock, generateKeyMock,
} = vi.hoisted(() => ({
    getSettingsMock: vi.fn(),
    updateSettingsMock: vi.fn(),
    testRemoteConnectionMock: vi.fn(),
    generateKeyMock: vi.fn(),
}))

vi.mock('../api', async (importOriginal) => {
    const actual = await importOriginal()
    return {
        ...actual,
        getSettings: getSettingsMock,
        updateSettings: updateSettingsMock,
        testRemoteConnection: testRemoteConnectionMock,
        generateTranscriptionRemoteKey: generateKeyMock,
    }
})

function baseSettings(overrides = {}) {
    return {
        transcription_provider: 'remote_with_fallback',
        transcription_remote_url: 'http://192.168.1.50:9000',
        transcription_remote_key: '',
        transcription_remote_timeout: 7200,
        auto_transcribe_enabled: false,
        whisper_model: 'medium',
        ...overrides,
    }
}

const KEY_PLACEHOLDER_TEXT = 'Shared secret for the Jetson server'

beforeEach(() => {
    getSettingsMock.mockReset().mockResolvedValue(baseSettings())
    updateSettingsMock.mockReset().mockResolvedValue({})
    testRemoteConnectionMock.mockReset()
    generateKeyMock.mockReset()
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
