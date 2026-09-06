/**
 * Console output for the reader's lifecycle (issue #278).
 *
 * The reader's `console.log` / `console.warn` calls are its only
 * observability in production, so they stay on there; under the test runner
 * they only bury the failures that matter, so they are silenced. Modules
 * extracted from `EbookReader` log through these instead of `console`
 * directly.
 */
export function makeLogger(sink, enabled) {
    return enabled ? (...args) => sink(...args) : () => {}
}

const enabled = import.meta.env?.MODE !== 'test'

export const debugLog = makeLogger((...args) => console.log(...args), enabled)
export const debugWarn = makeLogger((...args) => console.warn(...args), enabled)
