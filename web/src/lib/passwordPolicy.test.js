import { describe, it, expect } from 'vitest'
import {
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    PASSWORD_POLICY_MESSAGE,
    isPasswordValid,
    passwordError,
} from './passwordPolicy'

// Issue #205. These numbers are the web half of a three-way contract with
// server/schemas.py and android/.../PasswordPolicy.kt. Pinning them here means
// a drift shows up as a failing test rather than as a form that accepts what
// the API will 422.

describe('passwordPolicy', () => {
    it('is 8 to 128 characters, matching the server', () => {
        expect(PASSWORD_MIN_LENGTH).toBe(8)
        expect(PASSWORD_MAX_LENGTH).toBe(128)
    })

    it('names both bounds in the message', () => {
        expect(PASSWORD_POLICY_MESSAGE).toContain('8')
        expect(PASSWORD_POLICY_MESSAGE).toContain('128')
    })

    it('rejects one character under the floor and accepts the floor', () => {
        expect(isPasswordValid('a'.repeat(PASSWORD_MIN_LENGTH - 1))).toBe(false)
        expect(isPasswordValid('a'.repeat(PASSWORD_MIN_LENGTH))).toBe(true)
    })

    it('accepts the ceiling and rejects one character over it', () => {
        expect(isPasswordValid('a'.repeat(PASSWORD_MAX_LENGTH))).toBe(true)
        expect(isPasswordValid('a'.repeat(PASSWORD_MAX_LENGTH + 1))).toBe(false)
    })

    it('treats an empty or missing password as invalid', () => {
        expect(isPasswordValid('')).toBe(false)
        expect(isPasswordValid(undefined)).toBe(false)
        expect(isPasswordValid(null)).toBe(false)
    })

    it('returns the policy message for a bad password and null for a good one', () => {
        expect(passwordError('short')).toBe(PASSWORD_POLICY_MESSAGE)
        expect(passwordError('a'.repeat(PASSWORD_MIN_LENGTH))).toBeNull()
    })
})
