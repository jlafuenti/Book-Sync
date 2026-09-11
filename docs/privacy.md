# Tandem privacy policy

_Last updated: 2026-09-03_

Tandem is a self-hosted app. You point it at a Tandem server that you — or someone you
know — runs, and it reads the ebooks and audiobooks in that library. There is no Tandem
company, no Tandem cloud, and no account with us.

**No ads, no analytics, no crash-reporting SDK, no third-party trackers.** The app ships
none of them, and nothing about how you use it is sent to the people who wrote it.

## Who holds your data

**The operator of the server you sign in to.** They run the database that holds your
account, your library and your reading positions, and they decide how long any of it is
kept. If that is you, you are your own data controller. If it is someone else, they are
the person to ask about your data — the authors of Tandem have no access to it and
receive nothing from the app.

If you tap **Try the demo** on the first-run screen, you are signing in to a demo server
run by whoever built that copy of the app. The demo operator sees exactly what any other
operator sees, on a shared account other people are also using. Don't put anything
private in it.

## What the app sends, and where

### To your own server

Everything the app does with your library goes to the server address you entered, over
whatever connection that server uses (HTTPS unless you have deliberately allowed a plain
HTTP host on your own network):

- **Sign-in.** Your username and password when you log in; after that, tokens.
- **Reading and listening position.** Which book, which chapter, which sentence — plus,
  with every write, a **device name** and a **device id** so a position can be attributed
  to the right device. The device name defaults to your phone's make and model (for
  example "Google Pixel 9 Pro XL") and you can change it to anything you like under
  **Account → Device**; it is what your other devices show you in a book's session
  history. The device id is a random identifier the app generates on first launch and
  stores on the device. It is not your advertising id, and it is not any hardware or SIM
  identifier — reinstalling the app produces a new one.
- **Bookmarks and progress**, the same way.
- **Library, cover art, ebook and audiobook requests**, including streaming an audiobook
  while casting.
- **Sessions.** Each signed-in device gets its own session row on the server, holding that
  device id, when it signed in and when it was last used — that is what makes signing out
  on one device leave your other devices signed in. The row holds no password and no
  usable copy of any token.

Your server keeps a security **audit log** of events like sign-ins and password changes,
and those rows include the **IP address** the request came from. How long they are kept
is the operator's setting (`audit_log_retention_days`, 90 days by default; it can be set
to keep them forever).

### To one third party: the dictionary

When you select a word in the reader and tap **Define**, the app sends **that one word**
to `api.dictionaryapi.dev`, a free public dictionary service, and shows what comes back.

- Only the selected word is sent. Not the sentence, not the book, not your account.
- The request carries no login, no token and no device id — it is an anonymous, unsigned
  lookup made on a separate connection from the one your server uses.
- It happens only when you tap **Define**. Nothing is sent while you simply read.
- Like any web request, your device's IP address is visible to that service, and their
  terms and privacy practices are theirs, not ours: <https://dictionaryapi.dev/>
- **To avoid it entirely, don't use Define.** There is no other feature that reaches it.

This is the only outside destination hard-coded into the app, and a test in the repo
fails the build if a second one is ever added
(`server/tests/test_android_no_personal_hosts.py`).

### While casting

Casting to a Chromecast uses Google's Cast SDK, which looks for receiver devices on your
local network and communicates with them and with Google's Cast infrastructure. What
Google collects through the Cast SDK and through Google Play services is governed by
Google's own privacy policy. Audio itself is served either from your server, or — for a
book you have downloaded — directly from your phone over your local network to the
receiver, on a short-lived address that never leaves your LAN.

## What is stored on your device

- Your sign-in tokens and the server address you entered.
- A local database of your library, reading positions, and bookmarks.
- Any books you download for offline use, and their cover art.
- **Diagnostics.** If you turn on **Account → Diagnostics**, the app records its own log
  for a period you choose. If the app crashes, the crash details (the error, your app
  version, your Android version, and your device make and model) are written to that same
  local file. Nothing is uploaded. The log stays on your device until you delete it or
  uninstall — the only way it goes anywhere is if you choose **Report a problem** and
  share it yourself, to a recipient you pick. Read it before you send it.

**To remove all of it: uninstall the app.** Everything above lives in the app's own
storage and goes with it.

If you use Tandem in a browser instead, the equivalent is your sign-in tokens and a
cached copy of what you have been reading, held in that browser's storage for your
server's address. Clearing the site's data removes them.

## Deleting your account and server-side data

In the app, **Account → Delete account** deletes your account from your server, together
with your bookmarks, reading positions and per-device position hints. It asks for your
current password and for you to type `DELETE`, and it takes effect immediately.

Your operator's server also serves a page at `https://<your-server>/account-deletion`
that explains the same thing without needing a login, for someone who has already
uninstalled.

What survives: a single audit-log row recording that an account was deleted, with the
user id stripped out of it. Books in the library are not yours to delete — they belong
to the server, not to your account.

If you cannot sign in, ask the operator of your server; an administrator can delete the
account for you.

## Children

Tandem is not directed at children and collects nothing that identifies one. What a
Tandem server holds is whatever its operator's library holds.

## Changes

This policy lives in the Tandem repository and changes with the code. The date at the top
is the last time it changed; the full history is in git.

## Contact

Questions about the app itself: `support@tandembook.com`

Questions about your account, your library or your data: **the operator of the server you
sign in to**. We cannot see it and cannot act on it.
