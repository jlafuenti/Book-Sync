import { describe, it, expect } from 'vitest'
import { summarizeTroubleshoot, FIXED_BY_QUEUED_JOB } from './troubleshootSummary'

function issues(categories) {
    const total = Object.values(categories).reduce((n, rows) => n + rows.length, 0)
    return { categories, total }
}

const job = (pairId, status = 'pending') => ({ book_pair_id: pairId, status })

describe('summarizeTroubleshoot', () => {
    it('counts every issue as open when nothing is queued', () => {
        const s = summarizeTroubleshoot(issues({
            missing_cover: [{ item_id: 1 }, { item_id: 2 }],
        }), [])
        expect(s).toEqual({ total: 2, queued: 0, open: 2 })
    })

    it('leaves out transcript problems whose pair already has a job waiting', () => {
        // The word-timing backfill: hundreds of "out of step" rows, each
        // already queued for the re-transcription that fixes it.
        const s = summarizeTroubleshoot(issues({
            transcript_out_of_step: [{ pair_id: 10 }, { pair_id: 11 }, { pair_id: 12 }],
            missing_cover: [{ item_id: 1 }],
        }), [job(10), job(11, 'in_progress')])
        expect(s).toEqual({ total: 4, queued: 2, open: 2 })
    })

    it('treats partial transcripts and missing sync maps the same way', () => {
        const s = summarizeTroubleshoot(issues({
            transcript_partial: [{ pair_id: 20 }],
            sync_map_missing: [{ pair_id: 21 }],
        }), [job(20), job(21)])
        expect(s.open).toBe(0)
        expect(s.queued).toBe(2)
    })

    it('ignores finished, failed and cancelled jobs', () => {
        const s = summarizeTroubleshoot(issues({
            transcript_out_of_step: [{ pair_id: 10 }],
        }), [job(10, 'completed'), job(10, 'failed'), job(10, 'cancelled')])
        expect(s.open).toBe(1)
    })

    it('never discounts a broken file just because its pair is queued', () => {
        // A queued job does not fix a corrupt or missing file; Troubleshoot
        // flags those so the job can be pulled (#700). They stay open.
        const s = summarizeTroubleshoot(issues({
            audio_corrupt: [{ pair_id: 30, item_id: 5 }],
            missing: [{ pair_id: 30, item_id: 6 }],
        }), [job(30)])
        expect(s.open).toBe(2)
        expect(FIXED_BY_QUEUED_JOB).not.toContain('audio_corrupt')
    })

    it('copes with a missing queue or empty categories', () => {
        expect(summarizeTroubleshoot(issues({}), undefined)).toEqual({ total: 0, queued: 0, open: 0 })
        expect(summarizeTroubleshoot({ categories: {}, total: 3 }, null).open).toBe(3)
    })
})
