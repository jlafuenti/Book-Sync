/**
 * What the transcript heard during each sync point (issue #713).
 *
 * Sync points carry no heard text of their own - alignment never writes
 * `audio_text` - so the alignment view reads the pair's cached transcript and
 * gives each transcript sentence to the point whose `[start_ms, end_ms)` range
 * it starts in. A sentence starting in a gap between points, or in an empty
 * range, belongs to none. Returns a Map of point id -> the joined text.
 */
export function heardTextByPoint(points, sentences) {
    const out = new Map()
    if (!points?.length || !sentences?.length) return out
    const sorted = [...points].sort((a, b) => a.start_ms - b.start_ms)
    for (const s of sentences) {
        // The last point starting at or before this sentence.
        let lo = 0, hi = sorted.length - 1, at = -1
        while (lo <= hi) {
            const mid = (lo + hi) >> 1
            if (sorted[mid].start_ms <= s.start_ms) { at = mid; lo = mid + 1 } else { hi = mid - 1 }
        }
        // Several points can share a start (an empty range, then the real
        // one); walk back to one whose range actually holds the sentence.
        // Only across that shared start, so a sentence in a long gap costs one
        // step, not a walk back through the whole book.
        const shared = at >= 0 ? sorted[at].start_ms : null
        while (at >= 0 && sorted[at].start_ms === shared && !(s.start_ms < sorted[at].end_ms)) at--
        if (at < 0 || !(s.start_ms < sorted[at].end_ms)) continue
        const id = sorted[at].id
        out.set(id, out.has(id) ? `${out.get(id)} ${s.text}` : s.text)
    }
    return out
}
