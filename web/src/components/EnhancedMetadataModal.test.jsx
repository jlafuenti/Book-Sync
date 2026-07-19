import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import EnhancedMetadataModal from './EnhancedMetadataModal'

const { enrichAudiobookFromAbsMock } = vi.hoisted(() => ({
    enrichAudiobookFromAbsMock: vi.fn(),
}))

vi.mock('../api', () => ({
    uploadEbookCover: vi.fn(),
    uploadAudiobookCover: vi.fn(),
    applyRemoteCover: vi.fn(),
    rescanBook: vi.fn(),
    enrichAudiobookFromAbs: enrichAudiobookFromAbsMock,
}))

const book = { id: 1538, title: 'Antiagon Fire', author: 'L. E. Modesitt Jr' }

beforeEach(() => {
    enrichAudiobookFromAbsMock.mockReset()
    vi.spyOn(window, 'alert').mockImplementation(() => {})
})

describe('EnhancedMetadataModal enrich from ABS', () => {
    it('alerts about the tag-write failure and does not claim the file was updated', async () => {
        enrichAudiobookFromAbsMock.mockResolvedValue({
            status: 'tag_write_failed',
            message: "Metadata updated in the library, but writing tags to the file failed: 'utf-8' codec can't decode byte 0xc4",
            tag_write_error: "'utf-8' codec can't decode byte 0xc4",
        })
        const onClose = vi.fn()
        render(<EnhancedMetadataModal book={book} type="audiobook" onClose={onClose} onSave={vi.fn()} />)

        fireEvent.click(screen.getByRole('button', { name: /Enrich from ABS/ }))

        await waitFor(() => expect(window.alert).toHaveBeenCalled())
        const alertText = window.alert.mock.calls[0][0]
        expect(alertText).toMatch(/tag|file/i)
        expect(alertText).not.toMatch(/^Enriched from Audiobookshelf$/)
    })

    it('alerts success and closes the modal when enrichment and tag write both succeed', async () => {
        enrichAudiobookFromAbsMock.mockResolvedValue({
            status: 'enriched',
            message: 'Metadata enriched from Audiobookshelf and written back to file.',
            tag_write_error: null,
        })
        const onClose = vi.fn()
        render(<EnhancedMetadataModal book={book} type="audiobook" onClose={onClose} onSave={vi.fn()} />)

        fireEvent.click(screen.getByRole('button', { name: /Enrich from ABS/ }))

        await waitFor(() => expect(onClose).toHaveBeenCalled())
    })
})
