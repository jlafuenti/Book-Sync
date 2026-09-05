import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { ROLE_HIERARCHY, KNOWN_ROLES, roleMeets } from './roles'

/**
 * The role ladder fails closed on an unknown minimum (issue #359).
 *
 * `(ROLE_HIERARCHY[user.role] || 0) >= (ROLE_HIERARCHY[minRole] || 0)` scored an
 * unrecognised *minimum* as 0, and everybody clears a bar of 0. So
 * `<RequireRole min="admni">` did not fail the check, it removed it: the route
 * rendered for any signed-in account, nothing logged, and the page looked gated
 * in the source. Only a negative test with a low-privileged account would have
 * shown it.
 *
 * This is defence in depth — the server is the real barrier (`require_role`,
 * which now refuses an unknown floor at import) — but a console the user cannot
 * use is still a console they should not be shown.
 *
 * The five page tests that used to reimplement the fail-open expression in
 * their `useAuth` mocks now import `roleMeets` from here, so a typo behaves the
 * same under test as it does in the browser.
 */

describe('roleMeets', () => {
    it('orders the ladder superadmin > admin > editor > user', () => {
        expect(ROLE_HIERARCHY.superadmin).toBeGreaterThan(ROLE_HIERARCHY.admin)
        expect(ROLE_HIERARCHY.admin).toBeGreaterThan(ROLE_HIERARCHY.editor)
        expect(ROLE_HIERARCHY.editor).toBeGreaterThan(ROLE_HIERARCHY.user)
    })

    it('admits a role at or above the floor', () => {
        expect(roleMeets('editor', 'editor')).toBe(true)
        expect(roleMeets('admin', 'editor')).toBe(true)
        expect(roleMeets('superadmin', 'admin')).toBe(true)
    })

    it('refuses a role below the floor', () => {
        expect(roleMeets('user', 'editor')).toBe(false)
        expect(roleMeets('editor', 'admin')).toBe(false)
        expect(roleMeets('user', 'superadmin')).toBe(false)
    })

    it('refuses an unknown minimum, even for a superadmin', () => {
        expect(roleMeets('superadmin', 'admni')).toBe(false)
        expect(roleMeets('superadmin', 'editorr')).toBe(false)
        expect(roleMeets('superadmin', '')).toBe(false)
        expect(roleMeets('superadmin', undefined)).toBe(false)
        expect(roleMeets('superadmin', null)).toBe(false)
    })

    it('refuses an unknown user role — the direction that was already safe', () => {
        expect(roleMeets('wizard', 'user')).toBe(false)
        expect(roleMeets(undefined, 'user')).toBe(false)
    })
})

// ---------------------------------------------------------------------------
// Every role literal in web/src names a real role.
//
// The web is the more exposed of the two platforms: the minimum is an inline
// string repeated at ~20 call sites rather than three shared aliases on the
// server. The import-time guard has no equivalent here, so enumerate them.
// ---------------------------------------------------------------------------

/**
 * `hasMinRole('x')`, `<RequireRole min="x">`, `minRole: 'x'`.
 *
 * The `min=` pattern is anchored to `<RequireRole` on purpose — a bare `min=`
 * also matches `<input type="number" min="0">`, which is not a role gate.
 */
const LITERAL_PATTERNS = [
    /\bhasMinRole\(\s*['"]([^'"]*)['"]\s*\)/g,
    /<RequireRole\b[^>]*?\bmin=["']([^"']*)["']/g,
    /\bminRole:\s*['"]([^'"]*)['"]/g,
]

function sourceFiles(dir, acc = []) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name)
        if (entry.isDirectory()) {
            sourceFiles(full, acc)
        } else if (/\.(js|jsx)$/.test(entry.name) && !/\.test\.(js|jsx)$/.test(entry.name)) {
            acc.push(full)
        }
    }
    return acc
}

describe('role literals in web/src (issue #359)', () => {
    const root = path.join(process.cwd(), 'src')

    it('finds the call sites it claims to check', () => {
        // A silently-empty scan would pass for the wrong reason. There are ~20
        // literals today; if a rename drops this below a handful, this test has
        // stopped looking at anything and needs repointing, not deleting.
        let count = 0
        for (const file of sourceFiles(root)) {
            const src = fs.readFileSync(file, 'utf8')
            for (const re of LITERAL_PATTERNS) {
                count += [...src.matchAll(re)].length
            }
        }
        expect(count).toBeGreaterThan(10)
    })

    it('names only roles that exist', () => {
        const offenders = []
        for (const file of sourceFiles(root)) {
            const src = fs.readFileSync(file, 'utf8')
            for (const re of LITERAL_PATTERNS) {
                for (const m of src.matchAll(re)) {
                    if (!KNOWN_ROLES.includes(m[1])) {
                        offenders.push(`${path.relative(root, file)} -> ${JSON.stringify(m[1])}`)
                    }
                }
            }
        }

        expect(
            offenders,
            `Role gates naming a role that does not exist. Known roles: ${KNOWN_ROLES.join(', ')}. ` +
            'Since #359 an unknown minimum fails closed, so the symptom is a page ' +
            'nobody can reach rather than one everybody can — but it is still wrong.',
        ).toEqual([])
    })
})
