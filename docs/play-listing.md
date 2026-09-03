# Play Store listing

The listing text lives here so it is reviewable in a PR rather than only inside
the Play Console. Issue #175: the single most likely reason a reviewer marks
Tandem non-functional — or a new install leaves a one-star "doesn't work" —
is not knowing that the app is a client for a server you run yourself.

**The rule for every field below: "requires a self-hosted Tandem server" comes
first.** Not in a later paragraph, not implied. The in-app first-run screen
(`android/app/src/main/java/com/booksync/ui/auth/LoginScreen.kt`, `FirstRunScreen`)
says the same thing in the same order; keep the two in step.

## Status

Draft. The fields are not final and nothing here has been submitted.

## Short description (80 characters max)

> Requires a self-hosted Tandem server. Sync your place between ebook and audiobook.

## Full description

Opening line, before anything about features:

> Tandem requires a Tandem server that you (or someone you know) runs. It is not
> a bookstore or a subscription — it reads the ebooks and audiobooks in your own
> library. Set one up at https://github.com/jlafuenti/Book-Sync

Then the feature summary, drawn from `README.md`.

## Screenshot captions

The first screenshot's caption must repeat the requirement, because captions are
what a scrolling reviewer actually reads:

> Point the app at your own Tandem server

## Reviewer notes (Play Console → App access)

Supply a working server URL and test credentials, plus a one-line explanation
that sign-in is impossible without them. Do not paste credentials into this file
— it is public. They belong in the Console's App access section.

## Data safety

The app talks only to the server the user configures. No analytics SDK, no ads,
no third-party data sharing. The one outbound call to anything else is the
dictionary lookup in the reader; declare it if the form asks.

The user-facing statement of all of this is [privacy.md](privacy.md), which is
what the Console's **Privacy policy** field must point at (see "Privacy policy
URL" below). The section that follows is the same facts in the Console's own
vocabulary — keep the two in step, and keep both in step with the code.

### Data safety form (issue #150)

Answers below are derived from the sources, not from intent. The Android host
guard (`server/tests/test_android_no_personal_hosts.py`) fails the build if a
destination is added that these answers do not cover.

| Data type | Collected | Shared | Purpose | Linked to identity | Optional |
|---|---|---|---|---|---|
| Name / user IDs (username, email) | Yes — by the user's own server | No | Account management, app functionality | Yes | Required |
| Other user-generated content — *the selected word sent to the dictionary* | No | **Yes — third party** | App functionality | No | Yes (only when "Define" is tapped) |
| App activity — *reading and listening position, bookmarks* | Yes — by the user's own server | No | App functionality | Yes | Required |
| Device or other IDs — *app-generated install id + device name* | Yes — by the user's own server | No | App functionality (attributing a position to a device) | Yes | Required |
| Files and docs — *ebooks and audiobooks* | Already on the user's server; the app reads and caches them | No | App functionality | Yes | Required |

Everything else on the form is **No**: no location, no contacts, no photos, no
health data, no financial data, no messages, no advertising ID, no analytics of
any kind. Nothing is collected for advertising, marketing, personalisation,
fraud prevention or "developer communications".

Notes the form has no column for, but which the policy states and a reviewer may
ask about:

* **The developer receives nothing.** Every "collected" row above means collected
  *by the server the user chose*, which in almost every case is their own
  machine. Play's form has no way to express that; the privacy policy does, and
  the first-run screen says it before sign-in is even possible.
* **The dictionary is the only sharing.** One word, on demand, unauthenticated,
  to `api.dictionaryapi.dev`. No account, no token, no device id travels with it.
* **Diagnostics are local.** The timed log capture and the crash trace written on
  an uncaught exception stay in the app's own storage. Nothing is uploaded; the
  only way one leaves the device is the user choosing "Report a problem" and
  picking a recipient. That is not collection and must not be declared as such.
* **Cast.** While casting, Google's Cast SDK does its own discovery and
  communication. Declared as a dependency, not as Tandem data collection.

#### "Data is encrypted in transit" — answer with care

**Yes, with the caveat written into the policy.** The app's own connections are
HTTPS: the dictionary lookup always, and the user's server whenever they enter an
`https://` address — which is what a bare hostname is normalised to, and what
every documented deployment (`Caddyfile.example`, `docs/operations.md`) sets up.

The exception is deliberate and user-chosen: a build may allow cleartext HTTP to
named hosts via the `tandem.cleartextHosts` build setting, for someone running
Tandem on their own LAN with no certificate. **A Play build must ship that
setting empty.** Then the only cleartext the shipped app permits is loopback and
the emulator's host alias — neither of which is a network destination — and the
"encrypted in transit" answer is true without qualification. Verify before every
upload, and don't pass it with `-P` either:

```bash
grep -n "tandem.cleartextHosts" android/local.properties   # must be empty/absent
```

#### Data deletion

Answer **Yes** to both deletion questions and give the account-deletion URL.
Because Tandem is self-hosted there is no single such URL: it is
`https://<your-server>/account-deletion` on whichever server is supplied under
App access. The in-app path is **Account → Delete account**; what survives is one
audit-log row with the user id stripped out. Issue #146 is where that page and
that flow were added — keep this section in step with the detail it records.

### Privacy policy URL

Console field: **App content → Privacy policy**. It must be a public, login-free
URL that outlives any single deployment, so it is *not* served from an operator's
server. The repo publishes [privacy.md](privacy.md) via GitHub Pages:

> `https://<github-user>.github.io/Book-Sync/privacy.html`

Before submitting, open it in a logged-out browser (or a private window) and
confirm it renders — Play's crawler gets no more access than that.

## Content rating

The rating questionnaire is filled in by the Console; these are the answers and
the reasoning behind the ones that are not obvious.

| Question | Answer |
|---|---|
| Violence, sexuality, profanity, drugs, gambling, horror | **No** — none of it is *in the app* |
| Advertising | **No ads of any kind** |
| In-app purchases | **None** |
| Shares the user's location | **No** |
| Allows users to interact or exchange content | **No** — there is no messaging, no social feature, no shared account |
| User-generated content | **See below** |
| Digital purchases / user-generated content moderation tools | Not applicable — nothing is distributed through the app |

**On user-generated content, answer honestly rather than minimally.** The app
displays books from a server the user configures, and neither the app nor its
authors host, review, moderate or distribute any of that content — there is no
catalogue to police and no way for one user's content to reach another. It is
closer to a media player pointed at a folder than to a content platform. Say
that in the questionnaire's free-text box if it offers one; the reviewer's
concern is whether strangers can push content at each other, and here they
cannot.

The books themselves can be anything the user owns, including adult fiction. The
rating covers the app, not a library the app never sees — but if the
questionnaire asks whether the app can display mature content supplied by the
user, the answer is yes.
