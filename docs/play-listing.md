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

The server to supply is the public demo, never the personal one, and the text to
paste into the form is written out in [demo-server.md](demo-server.md) — which
also covers standing the demo up and the `playreview` account it needs (issue
#147). The release path this listing is part of, including the closed-test
requirement that has to be satisfied before any of it reaches production, is in
[release.md](release.md) (issue #173).

## Data safety

The app talks only to the server the user configures. No analytics SDK, no ads,
no third-party data sharing. The one outbound call to anything else is the
dictionary lookup in the reader; declare it if the form asks.
