/**
 * The one password policy, mirrored from the server (issue #205).
 *
 * The server is the enforcer — `schemas.password_field` in `server/schemas.py`
 * carries the same two numbers and rejects anything outside them with a 422.
 * This module exists so the form can say the same thing *before* the round
 * trip, and so the web, Android and the API stop disagreeing (they used to say
 * 6, 8 and 6 respectively).
 *
 * Keep these three in step:
 *   server/schemas.py                       PASSWORD_MIN_LENGTH / PASSWORD_MAX_LENGTH
 *   web/src/lib/passwordPolicy.js           this file
 *   android/.../ui/account/PasswordPolicy.kt
 */

export const PASSWORD_MIN_LENGTH = 8
export const PASSWORD_MAX_LENGTH = 128

export const PASSWORD_POLICY_MESSAGE =
    `Password must be between ${PASSWORD_MIN_LENGTH} and ${PASSWORD_MAX_LENGTH} characters.`

/** True when `password` satisfies the policy. */
export function isPasswordValid(password) {
    const length = (password || '').length
    return length >= PASSWORD_MIN_LENGTH && length <= PASSWORD_MAX_LENGTH
}

/** The message to show, or `null` when the password is acceptable. */
export function passwordError(password) {
    return isPasswordValid(password) ? null : PASSWORD_POLICY_MESSAGE
}
