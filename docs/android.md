# Android app

The Android client reads EPUBs (Readium), plays audiobooks (Media3), and shares one reading
position with the web app through the server. Requires **Android 8.0 (API 26)** or newer.

## Building

There is no published APK — build and sideload it yourself:

```bash
cd android && ./gradlew assembleDebug
```

The APK lands in `android/app/build/outputs/apk/debug/`. Use `assembleRelease` for a minified
build (you'll need your own signing config).

**Keep your keystore outside the checkout** and never name its path or password in a tracked
file — put both in `android/local.properties` or an environment variable. `.gitignore` refuses
`*.jks`/`*.keystore`/`*.p12` as a backstop, but a Play upload key cannot be rotated without
Google's help, so don't rely on the backstop.

Unit tests:

```bash
cd android && ./gradlew :app:testDebugUnitTest
```

## Pointing the app at your server

The app ships with **no server URL baked in**. On first launch the login screen opens with its
**Advanced** section already expanded and the Server URL field empty; Sign In stays disabled until
you fill it in. Enter your server's origin — e.g. `https://tandem.example.com` — and press
**Save & Restart**. The app restarts because Retrofit's base URL is fixed at startup.

You can change it later from **Account → Server URL**, same restart.

**Prefer HTTPS.** Android blocks cleartext HTTP unless the host is listed in the app's network
security config, which by default exempts only `localhost`, `127.0.0.1`, and the emulator's
`10.0.2.2`. A plain-HTTP server on your LAN needs to be added explicitly — see
[Machine-local build settings](#machine-local-build-settings) below. Android matches these entries
by hostname, **not** CIDR range, so "allow my whole subnet" isn't expressible; each host is listed
by name or address.

## Machine-local build settings

Nothing that identifies your machine — a server hostname, a LAN address — is committed. Two build
settings cover it, both defaulting to empty so a clean clone builds something generic:

| Setting | Effect |
|---|---|
| `tandem.defaultServerUrl` | Server the app starts on before one is configured. Becomes `BuildConfig.DEFAULT_SERVER_URL`. |
| `tandem.cleartextHosts` | Comma-separated extra hosts allowed to serve plain HTTP, on top of the loopback/emulator entries. |

Put them in **`android/local.properties`** — gitignored, and the conventional Android home for
machine-specific config:

```properties
tandem.defaultServerUrl=https://tandem.example.com
tandem.cleartextHosts=192.168.1.10
```

A Gradle property overrides the file, for CI or a one-off build:

```bash
cd android && ./gradlew assembleDebug -Ptandem.defaultServerUrl=https://tandem.example.com
```

`tandem.defaultServerUrl` applies only when nothing is stored yet — a URL you've already saved on
the device is never overwritten by a rebuild.

`tandem.cleartextHosts` feeds a generated `res/xml/network_security_config.xml` (the
`generateNetworkSecurityConfig` Gradle task). That file is **generated, not committed** — don't
look for it in `src/main/res/xml/`, and don't edit the copy under `app/build/`; change the setting
and rebuild. To see what your build produced:

```bash
cd android && ./gradlew :app:generateNetworkSecurityConfig && cat app/build/generated/res/generateNetworkSecurityConfig/xml/network_security_config.xml
```

`server/tests/test_android_no_personal_hosts.py` guards this: it fails if a private-network address
or a personal hostname reappears in the committed Android sources.

## Downloads and offline use

Books can be downloaded for offline use from a book's detail page and managed on the Downloaded
screen. For a pair you can fetch the ebook, the audio, or both — plus the pair's sync map, so
switching between reading and listening keeps working with no network. Unpaired ebooks and
audiobooks can be downloaded on their own too.

Writes made while offline (position updates, bookmarks) are queued locally in `pending_sync` and
drained when connectivity returns — on app start, when the network comes back, and when the
library screen loads. The drain is serialized behind a mutex so two triggers can't replay the same
write twice.

## Android Auto

The app exposes a media browse tree to Android Auto: the browse root is titled **Tandem**, and
playback resumption is handled so the car picks up whatever you were last listening to.

Auto behavior is hard to debug from the car, so the app can record a log: **Account →
Diagnostics** starts a timed capture on either the *Android Auto* or *Tandem App* channel, shows
an ongoing notification while it runs, and lets you share the resulting log file.

## Casting

Audio can be cast to a Chromecast. Because a cast receiver fetches media over plain HTTP with no
Authorization header, the server issues short-lived (15 minute) resource-scoped media tokens for
those URLs rather than exposing the JWT.

## Position sync

The Android app writes position through the same endpoint as the web app and follows the same
anchor rules — chapter + sentence index is portable, the Readium locator is a device-local hint.
Before changing anything in the reader's save/restore path, read
[docs/position-sync-contract.md](position-sync-contract.md); the failure mode (reopening at the
wrong page) is silent.

Sync-matching logic is duplicated on server and Android on purpose and pinned by shared golden
vectors in `server/tests/fixtures/sync_parity/`. Never change matcher behavior on one platform
alone.
