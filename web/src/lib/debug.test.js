import { describe, it, expect, vi } from 'vitest'
import { makeLogger, debugLog, debugWarn } from './debug'

describe('debug logging', () => {
    it('makeLogger forwards every argument to the sink when enabled', () => {
        const sink = vi.fn()
        makeLogger(sink, true)('[x]', 1, { a: 2 })
        expect(sink).toHaveBeenCalledWith('[x]', 1, { a: 2 })
    })

    it('makeLogger is a no-op when disabled', () => {
        const sink = vi.fn()
        makeLogger(sink, false)('[x]')
        expect(sink).not.toHaveBeenCalled()
    })

    it('the shared loggers are silent under the test runner', () => {
        const log = vi.spyOn(console, 'log').mockImplementation(() => {})
        const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
        debugLog('quiet')
        debugWarn('quiet')
        expect(log).not.toHaveBeenCalled()
        expect(warn).not.toHaveBeenCalled()
        log.mockRestore()
        warn.mockRestore()
    })
})
