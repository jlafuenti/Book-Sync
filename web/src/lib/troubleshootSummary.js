/**
 * How much of Troubleshoot Library still needs someone (the System page's
 * badge).
 *
 * Some issue rows are already being dealt with: a transcript out of step with
 * its audio, a partial transcript or a missing sync map is fixed by the
 * transcription job its pair has waiting or running. The word-timing backfill
 * put hundreds of such rows on the list at once, each already queued, and a
 * badge counting them would read as hundreds of problems. Those are counted as
 * `queued` instead of `open`.
 *
 * Only these categories: a corrupt, missing or DRM'd file whose pair is
 * queued is not fixed by the job (Troubleshoot flags it so the job can be
 * pulled, issue #700), so it stays open.
 */
export const FIXED_BY_QUEUED_JOB = ['transcript_out_of_step', 'transcript_partial', 'sync_map_missing']

const LIVE_JOB = new Set(['pending', 'in_progress'])

export function summarizeTroubleshoot(issues, queue) {
    const total = issues?.total || 0
    const livePairs = new Set(
        (queue || []).filter(q => LIVE_JOB.has(q.status)).map(q => q.book_pair_id)
    )
    let queued = 0
    for (const key of FIXED_BY_QUEUED_JOB) {
        for (const row of issues?.categories?.[key] || []) {
            if (livePairs.has(row.pair_id)) queued += 1
        }
    }
    return { total, queued, open: total - queued }
}
