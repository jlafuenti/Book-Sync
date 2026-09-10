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

`minSdk` is 26 (Android 8.0). `compileSdk` is **37** and `targetSdk` is pinned to **36** in
`android/app/build.gradle.kts`. They are deliberately different, and each moves for its own
reason:

- **`compileSdk` follows what the dependencies link against.** okhttp-android 5.5.0,
  `androidx.core` 1.19.0, `androidx.lifecycle` 2.11.0 and `androidx.hilt` 1.4.0 all fail
  `checkDebugAarMetadata` below 37, which left the app unable to take *any* library update while
  it sat on 36 (issue #454). Raising it is a build-time change and needs no device pass.
- **`targetSdk` is a behaviour switch, so it is pinned explicitly** — permissions,
  foreground-service rules, edge-to-edge and orientation handling all key off it, and bumping
  `compileSdk` for an unrelated reason must not drag it along (issue #148).

Google Play raises the floor for new apps and updates every year, roughly at the end of August;
API 36 became the minimum on 2026-08-31. When it moves again, raise `targetSdk` in its own change
and walk the behaviour changes for the new level on a device —
[target API level requirements](https://developer.android.com/google/play/requirements/target-sdk).
`BuildConfigPinsTest` fails if `targetSdk` leaves 36 without that deliberate step, if `compileSdk`
drops below the dependency floor, or if either falls below Play's floor.

The toolchain moves with `compileSdk`: AGP 9.0.x caps at `compileSdk` 36, so 37 requires
**AGP 9.4.0**, which in turn requires **Gradle 9.6.0** (`android/gradle/wrapper/`). AGP 9.4 is
built for Java 17 bytecode, so CI's JDK 17 still runs it.

## Pointing the app at your server

The app ships with **no server URL baked in**. On first launch the login screen opens with its
**Advanced** section already expanded and the Server URL field empty; Sign In stays disabled until
you fill it in. Enter your server's origin — e.g. `https://tandem.example.com` — and press
**Save**. It takes effect on the next request — no restart (issue #228; the base
URL is read per request by `BaseUrlInterceptor`).

A build made with the `tandem.demo*` settings below also offers **Try the demo** on that first
screen — one tap to a public demo server, for anyone who has not set one up yet. See
[demo-server.md](demo-server.md).

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

Nothing that identifies your machine — a server hostname, a LAN address, a password — is
committed. These build settings cover it, all defaulting to empty so a clean clone builds
something generic:

| Setting | Effect |
|---|---|
| `tandem.defaultServerUrl` | Server the app starts on before one is configured. Becomes `BuildConfig.DEFAULT_SERVER_URL`. |
| `tandem.cleartextHosts` | Comma-separated extra hosts allowed to serve plain HTTP, on top of the loopback/emulator entries. |
| `tandem.demoUrl` | Public demo server offered by the first-run screen's **Try the demo** button. Becomes `BuildConfig.DEMO_URL`. |
| `tandem.demoUser` | Demo account username. `BuildConfig.DEMO_USER`. |
| `tandem.demoPassword` | Demo account password. `BuildConfig.DEMO_PASSWORD`. |

Put them in **`android/local.properties`** — gitignored, and the conventional Android home for
machine-specific config:

```properties
tandem.defaultServerUrl=https://tandem.example.com
tandem.cleartextHosts=192.0.2.10
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

The three `tandem.demo*` settings are all-or-nothing (issue #147): set all three and the first-run
screen grows a **Try the demo** button that connects to that server and signs in with that account
in one tap; leave any of them blank — as a clean clone does — and no button is rendered at all.
`BuildConfigPinsTest` fails the build if any of the three is ever written into `build.gradle.kts`
as a literal instead of being read from a setting. Standing up the server they point at, and the
reason a *public* demo exists, are in [demo-server.md](demo-server.md).

**The demo password ships inside the APK**, where anyone can read it. That is accepted: the account
is `role=user` on a server holding nothing but public-domain books. Never point these settings at a
server whose contents matter.

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

## Backup and device transfer

The manifest opts into Auto Backup and Android 12+ device-to-device transfer, and both are
governed by an **include-list**: `res/xml/data_extraction_rules.xml` (API 31+) and
`res/xml/backup_rules.xml` (API 26-30), kept in step and pinned by `ManifestBackupRulesTest`.
Anything not named there never leaves the device. That is deliberate — an exclude-list has to be
updated every time a file is added and silently leaks whatever nobody remembered (issue #176).

What leaves: **shared preferences only** — reader and player settings. What stays: the DataStore
holding the access and refresh tokens, the server URL and the device id; the downloaded books; the
diagnostic logs.

**The Room database is excluded too, as of issue #364.** Room runs SQLite in WAL mode, so the
durable state at any instant is split between `booksync.db` and its `-wal` sidecar. A backup or
transfer taken without a checkpoint — or restored without the sidecar — produces a file that fails
`PRAGMA journal_mode` with `SQLITE_NOTADB`, and Android's default corruption handler responds by
**deleting it**. The next launch then starts from an empty database with nothing on screen to say
so: every unsynced position, the `pending_sync` offline queue and the acknowledged-items table
gone. That is the same silent wipe issue #168 removed `fallbackToDestructiveMigration` to stop.

Positions are server-synced, so what a restore now loses is a cache that refills on the next
sign-in. Putting the database back would mean a `BackupAgent` that checkpoints and switches to
`TRUNCATE` journalling before every backup, and that explicitly excludes the `-wal`/`-shm`
sidecars — a lot of machinery to make a cache survive. If you ever add one, update
`ManifestBackupRulesTest` in the same change; it currently fails if the `database` domain comes
back.

Corruption from any other cause is now at least visible: `corruptionLoggingOpenHelperFactory`
(`data/local/DatabaseCorruptionLogging.kt`) wraps Room's open-helper callback and appends one line
to the app diagnostics log before the platform deletes the file. It does **not** prevent the
deletion — refusing it would leave the app unable to open its own database — and it writes whether
or not a diagnostics capture is running, because nobody has one running when this fires.

## Android Auto

The app exposes a media browse tree to Android Auto. The root is titled **Tandem** and has exactly
two tabs, each holding leaf items — so the tree is two levels deep, and no node returns more than
`AUTO_MAX_ITEMS_PER_NODE` (100) rows:

```
[root] "Tandem"
├── continue_listening  — books with progress, most recently played first
└── library             — every book, alphabetical, pairs and standalone de-duplicated
```

Since streaming landed (issue #171) the Library tab lists books that are **not** downloaded too;
they stream from the server exactly as they do on the phone.

**Connecting to a car never starts playback.** `onPlaybackResumption` deliberately returns a failed
future for local playback, so Auto falls back to the browse UI and the driver presses play. This is
both a car app quality rule and a correctness one — the resumption path used to read a
SharedPreferences position that was only written at service shutdown, so a killed service resumed
at 0.

**Voice search** is implemented in two places that meet in the same matcher
(`com.booksync.auto.AutoSearch`): the session's `onSearch` / `onGetSearchResult` (what the car and
Assistant use), and `MEDIA_PLAY_FROM_SEARCH` on `MainActivity` (what a phone-side Assistant request
uses). Both end up as a `MediaItem` carrying only a search query, which `onSetMediaItems` resolves
to a `pair_N` / `audiobook_N` id. An empty query means "play something" and resolves to the most
recently played book.

**Browse content never waits on the server.** The rows come from Room. Cover art has a network rung
(issue #331), so the browse path fetches art in parallel under a 4 s budget and renders without it
if the server is slow or unreachable.

**An empty node always says why** rather than showing a blank list: "Open Tandem on your phone to
sign in" when there is no token, "No books yet…" when signed in with an empty library, "Nothing
started yet…" for an untouched Continue Listening tab.

Auto behavior is hard to debug from the car, so the app can record a log: **Account →
Diagnostics** starts a timed capture on either the *Android Auto* or *Tandem App* channel, shows
an ongoing notification while it runs, and lets you share the resulting log file.

### Desktop Head Unit walk-through (before every Play submission)

While the app is opted in to Android Auto distribution in Play Console, **every** submission is
reviewed against the [car app quality guidelines](https://developer.android.com/docs/quality-guidelines/car-app-quality),
and a failure there blocks the whole release, not just the Auto feature. Neither CI nor the emulator
can exercise Auto, so this pass is manual and has to be done by hand each time.

The JVM tests in `app/src/test/java/com/booksync/auto/` pin the parts that can be pinned —
tree shape, node caps, ordering, the empty-state leaf, search ranking, and (as source guards in
`AutoWiringTest`) the rules the service must keep. They are not a substitute for the walk-through;
they are what stops it from regressing between walk-throughs.

**Setup, once per machine**

1. Android Studio → **SDK Manager → SDK Tools** → check **Android Auto Desktop Head Unit Emulator**.
   It installs to `$ANDROID_HOME/extras/google/auto/`.
2. On the phone, enable Auto's developer mode: **Android Auto** settings → tap *Version* ten times →
   overflow → **Start head unit server**.
3. With the phone plugged in:

   ```bash
   adb forward tcp:5277 tcp:5277
   "$ANDROID_HOME/extras/google/auto/desktop-head-unit"     # .exe on Windows
   ```

**Checklist to walk (record the result in the release checklist)**

| # | Check | Pass looks like |
|---|---|---|
| 1 | Connect the phone with the app **not** running | The car shows the browse tree. Nothing starts playing on its own. |
| 2 | Browse root | Two tabs, "Continue Listening" and "Library", and the Tandem icon. |
| 3 | Open each tab | Content appears in a couple of seconds, well inside the ~10 s budget. |
| 4 | Open Library with a large library | Alphabetical, no book listed twice, and books that are not downloaded are present. |
| 5 | Play a downloaded book, then a book that is not downloaded | Both start; the second streams. |
| 6 | Pause, then resume from the car's transport controls | Resumes where it stopped. |
| 7 | Disconnect and reconnect mid-book | The book is at the top of Continue Listening at the right position, and **nothing auto-plays**. |
| 8 | Voice: "Play *&lt;a book title&gt;* on Tandem" | That book starts. |
| 9 | Voice: search by author or series name in Auto's search UI | Matching books are listed and playable. |
| 10 | Voice: "Play Tandem" with no title | The most recently played book starts. |
| 11 | Turn the server off (or airplane-mode the phone) and browse | Downloaded books still browse and play; nothing hangs waiting for the server. |
| 12 | Sign out on the phone, then browse in the car | A single row reading "Open Tandem on your phone to sign in" — not a blank list. |

Rows 1, 7, 11 and 12 are the ones most likely to fail a review, and rows 8–10 are the voice-actions
checklist item that used to be advertised in the manifest without being implemented at all.

If Auto ever becomes not worth the review dimension, **opting out is a Play Console listing change,
not a code change** — the browse tree keeps working for sideloaded users either way.

## Crash reporting

There is no crash SDK — no Crashlytics, no Sentry, nothing that ships data to a third party
(issue #230). What exists instead is two pieces:

- **Uncaught exceptions are written to the app diagnostics log.** `BookSyncApp.onCreate`
  installs `CrashLogHandler` as the process-wide default handler. It appends the stack trace
  plus app version/build, device model and Android version to `files/app_diagnostics.log` and
  then delegates to the handler it replaced, so the process still dies the way Android expects
  and Play Vitals still sees the crash. The write is **not** gated on diagnostics being
  enabled: nobody turns diagnostics on before a crash they did not know was coming.
- **Account → Report a problem** shares that log, with the same version/device facts repeated
  in the message body (share targets are free to drop attachments, and several do silently).

To see it work on a debug build: `adb shell am crash com.booksync`, then
`adb shell run-as com.booksync cat files/app_diagnostics.log` — the report is delimited by
`=== Tandem crash ===` / `=== end of crash ===`.

**Release builds are minified, so a trace is only readable with that release's `mapping.txt`.**
Upload `app/build/outputs/mapping/release/mapping.txt` to Play for every release you ship, and
keep a copy: it is what turns both a Vitals report and a user-shared log into named frames. A
report from a version whose mapping was never kept is close to unusable.

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

## Reader

`ReaderActivity` hosts Readium's `EpubNavigatorFragment`. Two things it does to the publication
before the navigator ever sees it are worth knowing about.

### The `<head/>` transformer

Readium 3.1.2 injects its stylesheet and its JavaScript into every XHTML resource by finding a
literal `</head>` and splicing its tags in front of it. A self-closing `<head/>` is well-formed
XHTML and has no such tag, so the injector throws `No </head> closing tag found in this resource`
*while the WebView is streaming the response*. The WebView renders its own error page for that
spine item — and since the tap that reveals the toolbar and the swipe that turns the page are
both driven by the JavaScript that never got injected, the reader is then a dead end for that
book, with only the system Back button. Calibre writes exactly this shape for the SVG cover title
page it generates, so it is not a rare content bug (issue #373).

`normalizeEpubHead` in `ui/reader/EpubHeadNormalizer.kt` rewrites `<head/>` to `<head></head>`
and returns its input untouched otherwise. It is deliberately the narrowest possible rewrite:
this code runs over every page of every book, so anything looser would be a general-purpose
content rewriter nobody asked for. The byte-level wrapper decodes as ISO-8859-1 — the one charset
that round-trips any byte sequence — so a UTF-8 resource comes back byte-identical.

It is wired in through `PublicationOpener(onCreatePublication = ...)`, which is the one hook
Readium 3.1.2 offers between parsing and building a publication: it hands over the
`Publication.Builder`, and `ReaderActivity.normalizeHeads` replaces `builder.container` with a
`TransformingContainer` that decorates XHTML/HTML entries only. There is no separate resource-
transformer registry in this version of the toolkit; the container wrapper *is* the supported
interposition point, and it sits below the navigator's `WebViewServer`, so the injector sees the
rewritten bytes. **The user's files are never modified** — the rewrite happens in memory, per
read.

### The escape hatch

Because any resource failure produces the same dead end, `ReaderActivity` also implements
Readium's `Navigator.Listener.onResourceLoadFailed`: it reveals the toolbar (which the missing
JavaScript can no longer do) and shows a snackbar, *This page could not be displayed*, with a
**Next chapter** action that calls `navigator.go(nextLink)`. `ResourceFailurePolicy` holds the
decision — which resource has already been reported, whether the bars need revealing, whether
there is a next resource at all — so it can be unit-tested without a WebView, the same way
`PositionSavePolicy` and `PageAudioHandoff` are.

One limit worth recording: Readium calls `onResourceLoadFailed` from `WebViewServer` when the
*container* fails to produce a resource, which is upstream of the HTML injection. An injector
failure like the `<head/>` one surfaces only as a broken response stream, so it does **not** reach
this callback. The transformer is what fixes that case; the escape hatch covers everything else
(a missing or unreadable spine item, a truncated download) and keeps any future variant a
nuisance rather than a lock-out.

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
