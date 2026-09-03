# Android app

The Android client reads EPUBs (Readium), plays audiobooks (Media3), and shares one reading
position with the web app through the server. Requires **Android 8.0 (API 26)** or newer.

## Building

There is no published APK — build and sideload it yourself:

```bash
cd android && ./gradlew assembleDebug
```

The APK lands in `android/app/build/outputs/apk/debug/`.

### Release builds and signing

`assembleRelease` (APK) and `bundleRelease` (AAB, the Play upload format) both run R8. Both
work on a clean clone with no configuration at all — they simply produce an **unsigned**
artifact, which is what CI builds on every PR so the minified path cannot rot unnoticed.

To produce a *signed* build, create an upload keystore **outside the checkout**:

```bash
keytool -genkeypair -v -keystore tandem-upload.jks -keyalg RSA -keysize 4096 -validity 10000 -alias upload
```

then add four properties to `android/local.properties` (gitignored, alongside
`tandem.defaultServerUrl`):

```properties
tandem.signing.storeFile=/absolute/path/to/tandem-upload.jks
tandem.signing.storePassword=...
tandem.signing.keyAlias=upload
tandem.signing.keyPassword=...
```

The signing config is applied **only when `storeFile` points at a file that exists**, so a
missing or partial configuration degrades to an unsigned build rather than a broken one.

**Keep the keystore outside the checkout** and never name its path or password in a tracked
file. `.gitignore` refuses `*.jks`/`*.keystore`/`*.p12` as a backstop, but a Play upload key
cannot be rotated without Google's help, so don't rely on the backstop — store it in a password
manager plus one offline copy.

**Keep `app/build/outputs/mapping/release/mapping.txt` for every release you ship** and upload
it to Play. Without it, Vitals crash reports are obfuscated stack traces and effectively
unreadable.

To smoke-test a minified build without the upload key, sign the release APK with the SDK's
debug keystore (password `android`, not a secret) — the R8 output is identical:

```bash
apksigner sign --ks ~/.android/debug.keystore --ks-pass pass:android   --out app-release-signed.apk app/build/outputs/apk/release/app-release-unsigned.apk
```

Unit tests:

```bash
cd android && ./gradlew :app:testDebugUnitTest
```

### SDK levels

`minSdk` is 26 (Android 8.0). `compileSdk` and `targetSdk` are both pinned to **36** in
`android/app/build.gradle.kts` — `targetSdk` explicitly, so that bumping `compileSdk` for an
unrelated reason cannot silently change runtime behaviour (permissions, foreground-service rules,
edge-to-edge, orientation handling all key off it).

Google Play raises the floor for new apps and updates every year, roughly at the end of August;
API 36 became the minimum on 2026-08-31. When it moves again, raise both values together and
re-check the behaviour changes for the new level —
[target API level requirements](https://developer.android.com/google/play/requirements/target-sdk).
`BuildConfigPinsTest` fails if either value drops below the floor.

## Pointing the app at your server

The app ships with **no server URL baked in**. On first launch the login screen opens with its
**Advanced** section already expanded and the Server URL field empty; Sign In stays disabled until
you fill it in. Enter your server's origin — e.g. `https://tandem.example.com` — and press
**Save**. It takes effect on the next request — no restart (issue #228; the base
URL is read per request by `BaseUrlInterceptor`).

You can change it later from **Account → Server URL**. Doing so **signs you out**:
the previous server's tokens are cleared rather than sent to the new host. Your
downloaded library is kept — it is stored per server and per user, so switching
back restores it without re-downloading.

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

## Streaming, downloads and offline use

**Audio streams by default.** Press play on any audiobook and it starts, whether or not it is on
the device — the player fetches `GET /api/files/audiobook/{id}` through the app's own OkHttp
client, so it carries the same `Authorization: Bearer` header as every other request and refreshes
the token mid-stream if it expires. Nothing goes in the URL: the server also accepts a scoped
`?token=` media token for consumers that cannot send a header, and Android deliberately does not
use it, because a URL ends up in logcat. Seeking works because the endpoint honours HTTP Range.

The rule is in `player/MediaSourceSelector.kt` and is the only place the app decides: **a
downloaded file if there is one, the server otherwise.** A downloaded book never touches the
network.

**Ebooks download on open.** An EPUB is small and Readium wants a real file, so opening a paired
ebook that is not on the device fetches it and opens the reader by itself, with progress and a
Cancel button rather than a prompt to press Download first.

**Downloading is for offline — and for Cast.** Books can be downloaded from a book's detail page,
from the player's "Download for offline" button, and managed on the Downloaded screen. For a pair
you can fetch the ebook, the audio, or both — plus the pair's sync map, so switching between
reading and listening keeps working with no network. Unpaired ebooks and audiobooks can be
downloaded on their own too.

Two things need the file rather than the stream:

- **Offline.** With no connection and no local copy, the transport controls are disabled and the
  player says so — that is the only state where they are.
- **Chromecast.** `LocalCastHttpServer` serves the phone's own copy to the receiver over the LAN,
  and a Cast receiver cannot send a bearer token, so there is no server-side Cast path. The Cast
  button is disabled until the book is downloaded, and the service refuses the handoff rather than
  handing the television a URL it cannot authenticate.

**Cleartext.** Streaming needs no separate allowance: it goes out on the same OkHttp client as the
API calls, and Android's network security config is process-wide, so a plain-HTTP server already
listed in `tandem.cleartextHosts` (see [Machine-local build settings](#machine-local-build-settings))
streams as well as it logs in. A host that is *not* listed cannot do either.

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

## Privacy

The user-facing statement of what leaves the device is **[privacy.md](privacy.md)**, and it is
the document Play's listing links to. In short: the app talks to the server the user configured
and to nothing else, with one exception — the reader's **Define** action sends the selected word
to `api.dictionaryapi.dev` on demand, unauthenticated, on its own OkHttp client with no Bearer
interceptor (`AppModule.provideDictionaryOkHttpClient`). No analytics, ads or crash-reporting SDK
is present in the build.

Two things carry an identity to the user's own server on every position write: the app-generated
install id from `DeviceIdManager` (a random UUID, not a hardware identifier) and the device name,
which defaults to `Build.MANUFACTURER + Build.MODEL` and is user-overridable.

`server/tests/test_android_no_personal_hosts.py` pins that `api.dictionaryapi.dev` stays the only
hard-coded third-party host under `app/src/main/java`. **A new destination fails that test on
purpose** — adding one means updating the policy and the Data safety answers in
[play-listing.md](play-listing.md) in the same change.

## Position sync

The Android app writes position through the same endpoint as the web app and follows the same
anchor rules — chapter + sentence index is portable, the Readium locator is a device-local hint.
Before changing anything in the reader's save/restore path, read
[docs/position-sync-contract.md](position-sync-contract.md); the failure mode (reopening at the
wrong page) is silent.

Sync-matching logic is duplicated on server and Android on purpose and pinned by shared golden
vectors in `server/tests/fixtures/sync_parity/`. Never change matcher behavior on one platform
alone.

## API version handshake

`SUPPORTED_API_VERSION` in `app/src/main/java/com/booksync/data/remote/VersionCompat.kt` is the
app's claim about what it needs from a server. It is compared against the `api_version` the server
reports from `GET /api/health`, once per process — on Home (the first screen with both a server and
a session) and on the first-run "Check connection", whichever happens first. A mismatch raises a
non-blocking banner naming the side that is behind; a server that reports no version at all raises
nothing.

**Bump `SUPPORTED_API_VERSION` only in the same change that starts requiring server behaviour an
older server does not have** — a field, an endpoint, or a parameter added on the server side that
this app now depends on. Bumping it without such a requirement tells every operator running a
perfectly good server to go upgrade it, and the banner stops being read.

When you do bump it:

1. Bump `API_VERSION` in `server/version.py` to the same number, in the same PR — the client's
   constant is meaningless unless a server actually advertises it.
2. Update the support window in [operations.md](operations.md) if the guidance there changes.
3. Expect operators to see the "upgrade the server" banner until they deploy. That is the point.

Neither number is a release counter: `versionName`/`versionCode` in `app/build.gradle.kts` and
`APP_VERSION` in `server/version.py` move every release, `API_VERSION` moves almost never.
