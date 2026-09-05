# The public demo server

Tandem is a client for a server you run. Anyone who has not run one — a Play
reviewer, a closed-test tester, someone who taps Install out of curiosity — meets
an empty **Server URL** field and stops there. Play calls that "app not
functional" and rejects it (issue #147), and the twelve testers the closed test
needs hit the same wall (issue #173).

The answer is one small public server holding nothing but public-domain books,
with one read-only account whose credentials are published on purpose. This page
is how to stand it up.

**It is not the personal server.** Putting a home-lab hostname in the Play
Console publishes it, invites traffic to it, and points strangers at a machine
holding a personal library. The demo is a separate VPS with a separate hostname
and nothing on it that matters.

> The hostname below is written `demo.example.com` throughout. Substitute your
> own; nothing in this repository may name a real host (pinned by
> `server/tests/test_android_no_personal_hosts.py`).

## What it has to survive

Not much, and that is the point:

- a Play reviewer signing in once, opening one book in each format;
- twelve testers doing roughly the same thing over fourteen days;
- whatever a public URL attracts once it is in the Play Console listing.

No transcription load — the one transcript is generated once, by hand, before
anyone else sees the server. No uploads: registration is closed, and the demo
account is `role=user`, which cannot reach the library-management endpoints.

## Sizing

| | |
|---|---|
| vCPU | 2 |
| RAM | 4 GB |
| Disk | 40 GB SSD |
| Bandwidth | 1 TB/month is far more than enough |

That is the smallest tier most providers sell above the 1 GB entry level, and it
is comfortable: Postgres, the FastAPI server and the nginx `web` container idle
in well under 1 GB. `docker-compose.example.yml` sets `mem_limit: 4g` on the
server container, so 4 GB total is the sane floor rather than a target.

Disk is dominated by audio: five LibriVox recordings at 64–128 kbps run
0.5–1.5 GB in total. 40 GB leaves room for the Docker images (~3 GB), Postgres,
covers and nightly backups.

**The one exception is the single transcription.** If you run it on this box (see
[One transcription](#one-transcription) below) it wants more CPU and RAM for a
few hours. Resize up for the afternoon and back down, or transcribe elsewhere.

## Build flags: both heavy options off

Two build arguments decide how large the server image is. The demo wants
**neither**:

- **`INSTALL_LOCAL_WHISPER` — off (the default).** The demo does not transcribe
  on demand; leaving it off keeps the image small and the box cheap. See
  [transcription.md](transcription.md).
- **`INSTALL_DRM_PLUGINS` — off (the default).** DeACSM/DeDRM exist for importing
  books you bought. A public demo has no DRM-protected books and no business
  carrying the plugins; `server/tests/test_repo_hygiene.py` already pins the
  default off. See [import-sources.md](import-sources.md).

So: `docker compose build` with no `--build-arg` at all. If you ever pass one to
this deployment, you are doing something the demo does not need.

## Compose and env

Start from the templates in the repository root, exactly as a self-hoster would:

```bash
git clone https://github.com/jlafuenti/Book-Sync.git
cd Book-Sync
cp docker-compose.example.yml docker-compose.yml
cp .env.example .env
```

Fill in `.env` (`POSTGRES_PASSWORD`), then edit `docker-compose.yml`. The
demo-specific changes to the template, all on the `server` service:

```yaml
  server:
    # Caddy terminates TLS on the host and proxies to these. Binding to loopback
    # keeps the un-proxied ports off the public internet entirely — see
    # docs/operations.md, "Reverse proxy".
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - ./library/ebooks:/data/ebooks
      - ./library/audiobooks:/data/audiobooks
      - ./data:/data/app
      - ./data/imports:/data/imports
      - ./backups:/backups
    environment:
      - APP_ENV=prod
      - JWT_SECRET_KEY=            # python -c "import secrets; print(secrets.token_urlsafe(64))"
      - CREDENTIAL_ENC_KEYS=       # python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
      - CORS_ORIGINS=https://demo.example.com
      - FORWARDED_ALLOW_IPS=172.16.0.0/12
      # Nobody signs up on the demo. Everyone uses the one published account, and
      # a public server with open registration collects junk accounts and pending
      # approvals nobody will ever action. See docs/operations.md, "Users".
      - ALLOW_PUBLIC_REGISTRATION=false
      # The demo never transcribes on request. `remote` with no worker configured
      # simply fails the job rather than pulling a Whisper model onto a 2-vCPU box.
      - TRANSCRIPTION_PROVIDER=remote
```

and on `web`:

```yaml
  web:
    ports:
      - "127.0.0.1:3000:3000"
```

`/backups` on a demo box may point at local disk. The template's warning about
putting backups on a different disk is about libraries that cannot be rebuilt;
this one can be rebuilt from Gutenberg and LibriVox in an hour.

## Caddy and Let's Encrypt

The repository ships [`Caddyfile.example`](../Caddyfile.example) — the
request-body limits in it are load-bearing (issue #151), so start from it rather
than writing a fresh one:

```bash
cp Caddyfile.example /etc/caddy/Caddyfile
```

Then replace the hostname and add an ACME contact address. The whole file, for
one host:

```caddyfile
{
	email you@example.com
}

demo.example.com {
	@big_uploads path \
		/api/library/upload/* \
		/api/library/ebooks/*/cover \
		/api/library/audiobooks/*/cover \
		/api/troubleshoot/replace/* \
		/api/import/acsm/upload
	handle @big_uploads {
		request_body {
			max_size 4GB
		}
		reverse_proxy localhost:8000
	}

	handle /api/* {
		request_body {
			max_size 16MB
		}
		reverse_proxy localhost:8000
	}

	handle {
		reverse_proxy localhost:3000
	}
}
```

Caddy obtains and renews the Let's Encrypt certificate by itself; the `email`
global option is the ACME account address, and the only prerequisites are that
`demo.example.com` resolves to the VPS on public DNS and that ports 80 and 443
reach it. Nothing else about TLS needs configuring.

Android will not talk to a plain-HTTP host that is not in the app's cleartext
allowlist, so HTTPS here is not optional — see [android.md](android.md),
"Pointing the app at your server".

`FORWARDED_ALLOW_IPS` above is what makes the login rate limit per-client rather
than one global bucket behind the proxy. The full reasoning is in
[operations.md](operations.md), "Reverse proxy" — read it before changing the
topology.

## The `playreview` account

1. Get the bootstrap superadmin password out of the logs on first boot:
   `docker compose logs server | grep -i superadmin`.
2. Sign in to `https://demo.example.com` as `admin` and change that password to
   something you keep in your password manager. It is **not** the demo password.
3. **System → User Management → add user**: username `playreview`, role `user`,
   a fixed password you are content to publish.

Role `user` matters. `admin` or `editor` would let anyone who installs the app
delete the demo library, scan directories, edit transcripts or read the audit
log — and everyone who installs the app has these credentials, because the
Android build embeds them (see below). `user` can read, listen and keep its own
reading position, which is the whole demo.

Two more properties the password needs:

- **Fixed.** Play requires sign-in details that stay "valid regardless of user
  location" for the life of the listing. Rotating it silently breaks every
  installed build's demo button and, sooner or later, a review.
- **Used nowhere else.** It goes in the Play Console, in the app binary, and in
  whatever forum post explains the demo.

Reset the demo account's reading positions occasionally if you care what the next
reviewer sees; nothing else on the server accumulates.

## The starter library

Five titles, each a Project Gutenberg ebook with a LibriVox recording of the same
work. **LibriVox recordings are public domain** — the project's volunteers
dedicate every recording to the public domain worldwide, so redistributing them
from a demo server needs no permission and no attribution (crediting the readers
is still the decent thing to do).

The Gutenberg *texts* here are all long out of copyright in the US. The Gutenberg
*edition* wraps them in a licence header and the "Project Gutenberg" trademark;
the licence itself says that stripping every reference to Project Gutenberg
leaves you with a plain public-domain work you may use freely. Either keep the
header intact or strip it — do not keep the trademark while editing the text
around it.

| Title | Ebook (EPUB) | Audiobook |
|---|---|---|
| *Alice's Adventures in Wonderland* — Lewis Carroll | https://www.gutenberg.org/ebooks/11 | https://librivox.org/alices-adventures-in-wonderland-by-lewis-carroll/ |
| *The Adventures of Sherlock Holmes* — Arthur Conan Doyle | https://www.gutenberg.org/ebooks/1661 | https://librivox.org/the-adventures-of-sherlock-holmes-by-sir-arthur-conan-doyle/ |
| *The Time Machine* — H. G. Wells | https://www.gutenberg.org/ebooks/35 | https://librivox.org/the-time-machine-by-h-g-wells/ |
| *Frankenstein* — Mary Shelley | https://www.gutenberg.org/ebooks/84 | https://librivox.org/frankenstein-or-the-modern-prometheus-by-mary-wollstonecraft-shelley/ |
| *The Yellow Wallpaper* — Charlotte Perkins Gilman | https://www.gutenberg.org/ebooks/1952 | https://librivox.org/the-yellow-wallpaper-by-charlotte-perkins-gilman/ |

Take the **EPUB (no images)** download from each Gutenberg page and the **64 kbps
MP3** zip from each LibriVox page (128 kbps if you would rather the audio sounded
good than the disk stayed small). LibriVox pages list several recordings of some
works — any one will do; prefer a single-reader version, because a solo narrator
transcribes more cleanly than a collaborative one.

Lay them out the way [library-conventions.md](library-conventions.md) describes,
so the auto-matcher pairs them without help:

```
library/ebooks/Lewis Carroll/Alice's Adventures in Wonderland.epub
library/audiobooks/Lewis Carroll/Alice's Adventures in Wonderland/01 - Down the Rabbit-Hole.mp3
...
```

Then **System → Library → Scan**, and check **Pairs**: all five should have
matched on title and author. Pair by hand anything that did not.

## One transcription

A paired book with no transcript still opens in both formats, but the thing
Tandem is *for* — switching between reading and listening without losing your
place — only shows up once there is a sync map. One book with one is enough for a
review; five would be five times the work for no extra demonstration.

Transcribe **The Yellow Wallpaper**. It is about forty minutes of audio, which is
the reason to pick it: everything else on the list is three hours or more.

Two ways to get it done without leaving local Whisper installed on the demo box:

- **Temporarily, on the VPS.** Resize to 4 vCPU / 8 GB, rebuild the server image
  once with `docker compose build --build-arg INSTALL_LOCAL_WHISPER=1`, set
  `TRANSCRIPTION_PROVIDER=local` and `WHISPER_MODEL=base`, queue the one job,
  wait, then rebuild without the flag, put `TRANSCRIPTION_PROVIDER` back to
  `remote`, and resize down. The transcript is stored in the database, so it
  survives all of that.
- **On a machine you already have.** Point the demo server at a remote worker in
  **System → Transcription Settings** (the transcriber URL is a runtime setting in
  the database, not an environment variable), run the one job, then clear it.
  Only worth it if such a worker already exists and can be reached from the VPS.

Either way, confirm afterwards in the web UI that the pair shows a sync map, and
on a phone that jumping from the reader to the player lands in the right place.
The demo is only doing its job if that works.

## Keeping it alive

Play's sign-in details must be valid for the whole life of the listing, not just
at submission. Two cheap habits:

- `restart: unless-stopped` is already in the template — keep it, so a VPS reboot
  brings everything back (issue #105).
- Before every submission, and once a month otherwise, sign in from a phone on
  mobile data with the published credentials and open one book in each format.
  That is the same check the reviewer will run.

Upgrades follow [operations.md](operations.md), "Upgrading". The demo should
usually run the same version as the Play build, so the version handshake stays
quiet on a reviewer's screen.

## Wiring the app to it

The Android app can carry the demo login and offer it as a **Try the demo** button
on the first-run screen, so a reviewer never types anything. Three build settings,
all empty by default so a clean clone ships nothing (issue #58) and shows no
button:

```properties
# android/local.properties — gitignored
tandem.demoUrl=https://demo.example.com
tandem.demoUser=playreview
tandem.demoPassword=...
```

or `-Ptandem.demoUrl=… -Ptandem.demoUser=… -Ptandem.demoPassword=…` for a one-off
build. Blank any one of the three and the button does not appear. Details in
[android.md](android.md), "Machine-local build settings".

The button probes `/api/health`, signs in — both at the demo address itself,
storing nothing — and only once the credentials have been accepted does it store
the server and leave the welcome screen. A demo server that is down, or a demo
password that has been rotated, therefore leaves the user exactly where they
were, with an error, and leaves the install unconfigured rather than pointed at a
server it cannot use.

The sign-in runs in an application-scoped coroutine rather than on the login
screen's, because storing the server URL re-creates that screen: the first
version was cancelled mid-flight by its own success and stopped dead after the
login response. The success is then published *before* the welcome screen is
dismissed, and the navigation is watched from `LoginScreen` rather than from the
welcome screen itself — dismissing swaps that screen out, so an observer living
inside it was torn down by the very event it was waiting for. If you change this
flow, read `DemoSignIn`: it carries the full account of both failures.

**These credentials are in the APK.** Anyone can extract them; that is accepted,
which is why the account is `role=user` on a server holding nothing private.

## Play Console → App content → App access

Choose **All or some functionality is restricted** and add one instruction set.
Text to paste, hostname and password substituted:

> **Name:** Demo server sign-in
>
> **Username:** playreview
>
> **Password:** *(the fixed demo password)*
>
> **Any other instructions:**
>
> Tandem is a client for a Tandem server that the user runs themselves, so the
> app asks for a server address before it asks for credentials. A public demo
> server is provided for review.
>
> The fastest route: on the first screen, tap **Try the demo**. That connects to
> the demo server and signs in with the account above — nothing needs to be typed.
>
> To do it manually instead: on the first screen, enter
> `https://demo.example.com` in **Tandem server address**, tap **Check
> connection**, then **Continue to sign in**, and sign in with the username and
> password above. The app does not need to be restarted.
>
> The demo account can read and listen; it cannot modify the library. The demo
> library is public-domain books from Project Gutenberg and LibriVox. Open
> *The Yellow Wallpaper* to see the ebook/audiobook position sync, which is what
> the app is for.

Keep this in step with [play-listing.md](play-listing.md) — the listing text and
the first-run screen make the same "you need a server" point, in the same order.

**No credentials in this repository.** The password belongs in the Play Console,
in `android/local.properties`, and in your password manager. Not here.
