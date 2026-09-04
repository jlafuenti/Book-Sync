import React from 'react'
import { Link } from 'react-router-dom'

/**
 * The public terms-of-use page (issue #262).
 *
 * The operator hosts other people's accounts and other people's book files on
 * personal hardware, with public registration on by default and — until this
 * page — nothing written down about what that does and does not promise. It is
 * also the natural home for the "you must be entitled to hold the content you
 * import" sentence that the ACSM/Audible paths in docs/import-sources.md need.
 *
 * Mounted before the auth gate in `App.jsx`, like the account-deletion page:
 * the audience is someone reading the registration form, who by definition has
 * no session. It therefore calls no API and reads nothing from localStorage.
 *
 * The prose is kept in step with `docs/terms.md` by hand — the repo copy is what
 * a reader of the source sees, this is what a user of a running server sees.
 * There is deliberately **no acceptance checkbox and no server-side field**: the
 * decision on issue #262 was to keep registration frictionless and treat this as
 * disclosure, not as a contract the server enforces.
 */
export default function TermsPage() {
    const h3 = { fontSize: '0.95rem', marginBottom: 4, marginTop: 18 }
    const p = { fontSize: '0.9rem' }

    return (
        <div className="login-page">
            <div className="login-card" style={{ maxWidth: 680, textAlign: 'left' }}>
                <h1 style={{ textAlign: 'center' }}>📖 Tandem</h1>
                <h2 style={{ fontSize: '1.1rem', marginTop: 8 }}>Terms of use</h2>

                <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
                    Tandem is self-hosted. There is no company and no central service — only an
                    account on this particular server, run by a person on hardware they own. These
                    terms are between you and whoever runs it. The people who wrote the software
                    are not a party to them.
                </p>

                <h3 style={h3}>The service is personal and best-effort</h3>
                <p data-testid="terms-no-warranty" style={p}>
                    This server is a personal project, not a product. It is provided as-is, with
                    <strong> no warranty of any kind</strong> and <strong>no uptime guarantee</strong>:
                    it may be down, slow, restarted mid-chapter, or switched off for good, with or
                    without notice. Backups may exist but are not promised to you, and a restore can
                    lose recent reading positions.
                </p>

                <h3 style={h3}>Upload only content you are entitled to hold</h3>
                <p data-testid="terms-your-content" style={p}>
                    You may put a file on this server only if you are <strong>legally entitled to
                    hold a copy of it</strong> — books you bought, books you borrowed under a licence
                    that lets you keep a local copy, public-domain works, or your own writing. This
                    applies in full to the Adobe (ACSM) and Audible import paths, which exist so that
                    you can read your own purchases on your own hardware. Do not upload anything you
                    do not have the right to hold, and do not redistribute what you upload. The
                    operator can remove any file at any time without asking first.
                </p>

                <h3 style={h3}>Accounts</h3>
                <p data-testid="terms-accounts" style={p}>
                    An account has to be approved by the operator before it works, and approval is
                    entirely at their discretion. The operator may <strong>remove your account</strong>
                    {' '}and its data at any time, for any reason or none — including because the
                    server is being shut down. There is no appeal and no export guarantee. You can
                    leave the same way: ask the operator to delete your account. Do not attempt to
                    reach other users' data, and do not try to break or work around the server's
                    limits.
                </p>

                <h3 style={h3}>Your data</h3>
                <p data-testid="terms-data" style={p}>
                    What is stored, why, and for how long is described in the server's privacy
                    policy. Where these terms and that document overlap, the privacy policy is the
                    more specific one and wins.
                </p>

                <h3 style={h3}>Changes</h3>
                <p style={p}>
                    These terms can change, and the version on this page is the one that applies.
                    There is no notification mechanism.
                </p>

                <h3 style={h3}>Contact</h3>
                <p data-testid="terms-contact" style={p}>
                    Takedown requests, deletion requests and anything else about this server go to
                    its operator: <strong>&lt;contact email — to be filled in&gt;</strong>. If you do
                    not know who runs your server, the address the app points at is the place to
                    start.
                </p>

                <p style={{ marginTop: 20, textAlign: 'center' }}>
                    <Link to="/">Sign in to Tandem</Link>
                </p>
            </div>
        </div>
    )
}
