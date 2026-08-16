# Web app as a PWA

The web UI is a Progressive Web App (issue #62): it exposes playback to the
phone's lock screen, installs to the home screen, and precaches its own shell
so it opens instantly. There is no iOS app, so for an iPhone this *is* the app.

## Lock-screen / hardware controls (Media Session)

`web/src/contexts/AudioPlayerContext.jsx` publishes the playing book to
`navigator.mediaSession` through the wrappers in `web/src/lib/mediaSession.js`:

- **Metadata** — title, author, and the cover (resolved through `coverSrc()`,
  i.e. the same short-lived scoped media token every other `<img>` uses; the
  long-lived access token 401s against `/api/files/covers` — issue #121).
- **Actions** — `play`, `pause`, `stop`, `seekbackward`, `seekforward`,
  `seekto`. Each calls the *same* context function as the on-screen button, so
  the OS controls inherit the contract: a lock-screen resume rewinds 5 s, a
  lock-screen skip is 30 s in either direction (not the OS default of 10/15),
  and every action is an explicit user command that claims `source`. See
  `docs/position-sync-contract.md` § Playback offsets.
- **Position state** — fed from `timeupdate` (throttled to ~1 s) and
  immediately on seek/skip/speed change, so the lock-screen scrubber tracks.
- **Playback state** — `playing`/`paused` mirror the element; `stop()` clears
  the whole session so the OS drops the controls.

All of it feature-detects: without `navigator.mediaSession` the helpers are
no-ops.

## Installable (manifest)

`web/public/manifest.webmanifest` is hand-written (not generated): name
"Tandem", `start_url: /continue`, `display: standalone`, theme/background
colours matching the default Blueprint theme, and 64/192/512 PNG icons plus a
maskable 512. `index.html` links it and adds the iOS bits (`apple-touch-icon`,
`apple-mobile-web-app-*`). `<meta name="theme-color">` is set before first
paint from the stored theme and kept in step by `applyTheme()` so the status
bar / task switcher follow the chosen theme.

Icons live in `web/public/` and were generated once from `public/icon.svg`
(a copy of the Blueprint favicon):

```bash
cd web && npx --yes @vite-pwa/assets-generator --preset minimal-2023 public/icon.svg
```

`src/pwa/manifest.test.js` pins the manifest shape and that every icon file
exists.

## Service worker (app shell only)

`vite-plugin-pwa` (config in `web/vite.config.js`) generates `dist/sw.js` at
build time. Policy:

| What | Strategy |
|---|---|
| Built shell — `index.html`, hashed JS/CSS, icons, favicons | **Precached**; navigations fall back to `/index.html` |
| `/api/**` | **Network only** — never cached, never a navigation fallback (`navigateFallbackDenylist`) |
| Audio / ebook / cover responses | **Not cached** — range requests plus 15-minute media tokens; offline media is a separate future feature |

`registerType: 'autoUpdate'` + `clientsClaim`/`skipWaiting`: a new deploy is
picked up on the next load with no "refresh to update" prompt.

Registration is `web/src/pwa/registerSw.js`, called from `main.jsx`, and runs
**only in production builds** (`import.meta.env.PROD`) — the dev server (HMR)
and vitest never see a worker.

`web/Dockerfile` sets the nginx cache headers the worker needs: `sw.js`,
`workbox-*.js`, `index.html` and the manifest are `no-cache, must-revalidate`
(the worker's update check must always see the latest), and `/assets/*` (Vite
content-hashed) are `immutable, max-age=1y`.

### Debugging

- Chrome: DevTools → Application → Service Workers (tick "Update on reload"
  while developing against a production build); "Manifest" panel shows
  installability errors.
- Force a clean slate: Application → Storage → "Clear site data".
- If a deploy seems stuck on an old shell, check that `/sw.js` really comes
  back with `Cache-Control: no-cache` — a proxy in front of nginx caching it
  is the usual culprit.

### Manual verification after a deploy

1. Android Chrome and iOS Safari: play a book, lock the phone → title, author,
   cover and controls appear; skip buttons move 30 s; play from the lock screen
   resumes 5 s back.
2. Add to Home Screen → opens standalone with the Tandem icon and name;
   status bar colour matches the theme.
3. Airplane mode → the app shell still loads to the login/home screen; API
   calls fail gracefully.
4. Desktop: hardware media keys drive play/pause; the player bar cover loads
   (no 401 in the console — issue #121).
