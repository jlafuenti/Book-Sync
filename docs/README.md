# Tandem documentation

Every document in this directory, grouped by what you are trying to do. This page is the single
index — the root [README](../README.md) points here rather than keeping a second list, and
`server/tests/test_docs_contract.py` fails if a file under `docs/` is missing from the table below
or if a link here points at a file that does not exist.

## Using it

| Doc | Covers |
|---|---|
| [account-deletion.md](account-deletion.md) | How to delete an account and its data, wherever the account lives — the URL the Play Console requires |
| [library-conventions.md](library-conventions.md) | Supported formats, folder/filename patterns, metadata precedence, auto-pairing rules, format conversion |
| [import-sources.md](import-sources.md) | ACSM (Adobe ADEPT) and Audible import pipelines, and the opt-in `INSTALL_DRM_PLUGINS` build flag |
| [transcription.md](transcription.md) | Provider modes, queue behavior, off-hours window, what affects runtime |
| [android.md](android.md) | Building the app, pointing it at your server, downloads/offline, Android Auto |
| [web-pwa.md](web-pwa.md) | Web app as a PWA: lock-screen controls, home-screen install, service-worker caching policy |

## Running it

| Doc | Covers |
|---|---|
| [operations.md](operations.md) | Logs, upgrades, reverse-proxy setup, password rotation, storage, restart policies, account deletion |
| [backup-restore.md](backup-restore.md) | Backup schedule, restore procedure, test drills |
| [../jetson/README.md](../jetson/README.md) | Deploying the remote transcription worker on a Jetson Orin Nano |

## Building on it

| Doc | Covers |
|---|---|
| [api.md](api.md) | The HTTP API: base URL, auth and sessions, media tokens, roles, route families, error shapes |
| [openapi.json](openapi.json) | The generated OpenAPI document — every route, request body and response model. Regenerate with `server/scripts/export_openapi.py` |
| [testing.md](testing.md) | Test suites, fixtures, coverage policy |
| [position-sync-contract.md](position-sync-contract.md) | The cross-device position rules — read before touching bookmark/progress writes |
| [request-transactions.md](request-transactions.md) | Who commits: `get_db` vs an explicit `db.commit()`, and when a handler may do it itself |

## Project and release

| Doc | Covers |
|---|---|
| [terms.md](terms.md) | Terms of use for a hosted Tandem server — served at `/terms` and linked from both registration forms |
| [releasing.md](releasing.md) | Cutting a release: version bump, tag, GitHub Release, deploy, and the rollback runbook |
| [privacy.md](privacy.md) | The privacy policy, published from here to the public site — what leaves the device, and who holds it |
| [play-listing.md](play-listing.md) | Draft Play Store listing text, the Data safety form answers (including the `/account-deletion` link Play requires) and the content-rating notes — kept in the repo so they are reviewable |
| [release.md](release.md) | The Play release path: closed-test requirement, timeline, and the prerequisites to clear first |
| [history-rewrite-runbook.md](history-rewrite-runbook.md) | One-time git history rewrite to run immediately before the repo is made public |
