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

// The transcription worker (jetson/server.py) is deployed by hand, separately
// from the server. It reports the release it was built from, the server
// compares it with its own, and this says when the worker is behind. It is the
// operator's own machine, so it renders whether or not the GitHub check is on.
const WORKER_QUIET = {
    unreachable: "Couldn't reach the transcription worker to read its version. It will try again later.",
    unauthorized: "The transcription worker rejected this server's key, so its version could not be read — check the shared secret under Transcription.",
    unreported: 'The transcription worker does not report a version; it predates this check. Rebuild it from the current release to be sure it matches.',
    unrecognised_version: "The transcription worker reported a version that wasn't recognised.",
}

function WorkerLine({ worker }) {
    if (!worker || !worker.configured) return null

    if (worker.status === 'behind') {
        return (
            <div
                className="alert alert-info"
                role="status"
                aria-label="Transcription worker"
                style={{ marginBottom: 16, flexWrap: 'wrap' }}
            >
                <div style={{ flex: 1, minWidth: 220 }}>
                    <strong>The transcription worker is on {worker.version}</strong>
                    {' '}— this server is {worker.server_version}.
                    <div style={{ fontSize: '0.85rem', marginTop: 4 }}>
                        On the worker, check out the same release and rebuild:{' '}
                        <code>git pull</code> then <code>docker compose up -d --build</code>{' '}
                        (see <code>jetson/README.md</code>).
                    </div>
                </div>
            </div>
        )
    }

    if (worker.status === 'current') {
        return <Quiet>Transcription worker up to date · {worker.version}</Quiet>
    }

    const quiet = WORKER_QUIET[worker.reason]
    return quiet ? <Quiet>{quiet}</Quiet> : null
}

function ReleaseLine({ status, onEnable, onDecline }) {
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

export default function UpdateCheckBanner({ status, onEnable, onDecline }) {
    if (!status) return null
    return (
        <>
            <ReleaseLine status={status} onEnable={onEnable} onDecline={onDecline} />
            <WorkerLine worker={status.worker} />
        </>
    )
}
