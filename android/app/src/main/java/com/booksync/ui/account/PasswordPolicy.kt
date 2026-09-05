package com.booksync.ui.account

/**
 * The one password policy, mirrored from the server (issue #205).
 *
 * The server is the enforcer — `password_field` in `server/schemas.py` carries
 * the same two numbers and answers 422 for anything outside them. This object
 * exists so the sheet can fail locally the same way the API does, and so the
 * three surfaces stop disagreeing: the web forms said 6, this sheet said 8 and
 * the API said 6, so a password the phone refused was one the browser accepted.
 *
 * Keep these three in step:
 *   server/schemas.py                       PASSWORD_MIN_LENGTH / PASSWORD_MAX_LENGTH
 *   web/src/lib/passwordPolicy.js
 *   android/.../ui/account/PasswordPolicy.kt  this file
 */
object PasswordPolicy {

    const val MIN_LENGTH = 8

    /**
     * bcrypt only hashes the first 72 *bytes* of its input, so a ceiling is a
     * cost guard on the hash rather than a security boundary; 128 characters
     * is deliberately roomy enough for a real passphrase.
     */
    const val MAX_LENGTH = 128

    /** The exact wording the server and the web form use. */
    const val MESSAGE = "Password must be between $MIN_LENGTH and $MAX_LENGTH characters."

    /** True when [password] satisfies the policy. */
    fun isValid(password: String): Boolean = password.length in MIN_LENGTH..MAX_LENGTH

    /** [MESSAGE] when [password] fails the policy, or null when it passes. */
    fun errorFor(password: String): String? = if (isValid(password)) null else MESSAGE
}
