/**
 * What the System page says about Tandem releases (issue #463).
 *
 * The server asks GitHub for the latest release and compares its version with
 * the one it is running (`server/services/update_check.py`). This only renders
 * that answer.
 *
 * Opt-in. When enabled the server contacts api.github.com, and docs/privacy.md
 * promises no outbound call nobody asked for — so until the admin answers, the
 * page asks instead of checking.
 *
 * Notify-only. Updating from inside the app would need the Docker socket, which
 * would undo the container hardening of issue #180, so the banner gives the steps.
 */

// The only link this renders comes from GitHub's API by way of the server.
// Refresh tokens live in localStorage (CLAUDE.md, issue #286), which makes a
// `javascript:` href account takeover rather than a broken link — so nothing
// but the project's own release pages is ever linked.
const RELEASE_URL_PREFIX = 'https://github.com/jlafuenti/Book-Sync/releases/'

const QUIET_REASONS = {
    not_checked_yet: 'Checking for updates…',
    no_releases: 'No releases have been published yet.',
    rate_limited: "Couldn't check for updates — GitHub is rate-limiting this server. It will try again later.",
    unreachable: "Couldn't check for updates — GitHub could not be reached. It will try again later.",
    unrecognised_version: "Couldn't check for updates — the latest release's version was not recognised.",
}

function Quiet({ children }) {
    return (
        <p className="system-form-toggle-hint" style={{ margin: '0 0 12px' }}>
            {children}
        </p>
    )
}

export default function UpdateCheckBanner({ status, onEnable, onDecline }) {
    if (!status) return null

    if (!status.enabled && !status.prompted) {
        return (
            <div className="alert alert-info" style={{ marginBottom: 16, flexWrap: 'wrap' }}>
                <div style={{ flex: 1, minWidth: 220 }}>
                    <strong>Check for updates automatically?</strong>
                    <div style={{ fontSize: '0.85rem', marginTop: 4 }}>
                        The server will ask GitHub about new Tandem releases every few hours.
                        GitHub sees this server&apos;s address; nothing else is sent.
                    </div>
                </div>
                <button className="btn btn-sm btn-primary" onClick={onEnable}>Enable</button>
                <button className="btn btn-sm btn-secondary" onClick={onDecline}>No thanks</button>
            </div>
        )
    }

    if (!status.enabled) return null

    if (status.status === 'available') {
        const tag = `v${status.latest_version}`
        const safeUrl = typeof status.release_url === 'string'
            && status.release_url.startsWith(RELEASE_URL_PREFIX)
            ? status.release_url
            : null
        return (
            <div className="alert alert-info" role="status" style={{ marginBottom: 16, flexWrap: 'wrap' }}>
                <div style={{ flex: 1, minWidth: 220 }}>
                    <strong>Tandem {status.latest_version} is available</strong>
                    {' '}— you&apos;re running {status.running_version}.
                    {safeUrl && (
                        <>
                            {' '}
                            <a href={safeUrl} target="_blank" rel="noopener noreferrer">Release notes</a>
                        </>
                    )}
                    <div style={{ fontSize: '0.85rem', marginTop: 4 }}>
                        To update, check out <code>{tag}</code> on the server and run{' '}
                        <code>docker compose up -d --build</code>.
                    </div>
                </div>
            </div>
        )
    }

    if (status.status === 'current') {
        return <Quiet>Up to date · Tandem {status.running_version}</Quiet>
    }

    const quiet = QUIET_REASONS[status.reason]
    return quiet ? <Quiet>{quiet}</Quiet> : null
}
