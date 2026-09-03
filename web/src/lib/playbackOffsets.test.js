import { describe, it, expect } from 'vitest'
import { SKIP_SECONDS, RESUME_REWIND_SECONDS, handoffPositionMs } from './playbackOffsets'

// The web half of the cross-platform offsets contract (issue #42). Android's
// PlaybackOffsetsTest and the server's
// test_epub_to_audio_exact_match_applies_default_rewind assert the same two
// numbers literally, so a one-sided edit fails CI instead of shipping a silent
// divergence. See docs/position-sync-contract.md § Playback offsets.
describe('playback offsets', () => {
    it('skips 30s and rewinds 5s, the same numbers Android and the server use', () => {
        expect(SKIP_SECONDS).toBe(30)
        expect(RESUME_REWIND_SECONDS).toBe(5)
    })
})

describe('handoffPositionMs (issue #212)', () => {
    it('lands the resume rewind before the anchor', () => {
        expect(handoffPositionMs(42000)).toBe(37000)
    })

    it('clamps at 0 rather than seeking negative near the start of the book', () => {
        expect(handoffPositionMs(2000)).toBe(0)
        expect(handoffPositionMs(0)).toBe(0)
    })

    it('treats a missing anchor as the start of the book', () => {
        expect(handoffPositionMs(null)).toBe(0)
        expect(handoffPositionMs(undefined)).toBe(0)
    })
})
