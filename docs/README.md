# Tandem documentation

Every document in this directory, grouped by what you are trying to do. This page is the single
index — the root [README](../README.md) points here rather than keeping a second list, and
`server/tests/test_docs_contract.py` fails if a file under `docs/` is missing from the table below
or if a link here points at a file that does not exist.

## Using it

| Doc | Covers |
|---|---|
| [library-conventions.md](library-conventions.md) | Supported formats, folder/filename patterns, metadata precedence, auto-pairing rules, format conversion |
| [import-sources.md](import-sources.md) | ACSM (Adobe ADEPT) and Audible import pipelines, and the opt-in `INSTALL_DRM_PLUGINS` build flag |
| [transcription.md](transcription.md) | Provider modes, queue behavior, off-hours window, what affects runtime |
| [android.md](android.md) | Building the app, pointing it at your server, downloads/offline, Android Auto |
| [web-pwa.md](web-pwa.md) | Web app as a PWA: lock-screen controls, home-screen install, service-worker caching policy |

## Running it

| Doc | Covers |
|---|---|
| [operations.md](operations.md) | Logs, upgrades, reverse-proxy setup, password rotation, storage, restart policies |
| [backup-restore.md](backup-restore.md) | Backup schedule, restore procedure, test drills |
| [../jetson/README.md](../jetson/README.md) | Deploying the remote transcription worker on a Jetson Orin Nano |

## Building on it

| Doc | Covers |
|---|---|
| [testing.md](testing.md) | Test suites, fixtures, coverage policy |
| [position-sync-contract.md](position-sync-contract.md) | The cross-device position rules — read before touching bookmark/progress writes |

## Project and release

| Doc | Covers |
|---|---|
| [play-listing.md](play-listing.md) | Draft Play Store listing text — kept in the repo so it is reviewable |
| [history-rewrite-runbook.md](history-rewrite-runbook.md) | One-time git history rewrite to run immediately before the repo is made public |
