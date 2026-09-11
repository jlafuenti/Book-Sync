# Deleting your account and your data

Tandem has no central account. Your account exists **only on the server you sign in to**, which is run by you or by whoever invited you — not by the app's developer. Deleting it is therefore something that server does, and this page explains how to get it done wherever your account lives.

## If you run the server

You hold the data, so you control its removal.

- **Delete one account:** sign in as an administrator and go to **System → User Management**, then delete the user. That removes the account together with its reading positions, bookmarks and progress.
- **Delete everything:** stop the stack and remove its database volume and data directory. See [backup and restore](backup-restore.md) for exactly what lives where.

## If someone else runs the server

Ask that server's operator — the person who gave you the address or the invite. They can delete your account from the same **User Management** page, and they are the only person who can: the developer of the app has no access to their server and no copy of its data.

Every Tandem server also publishes a deletion page at `/account-deletion` on its own address, which names the operator's contact route.

## If you tried the public demo

The demo server at `demo.tandembook.com` has a single shared, read-only account that everyone uses. It is not personal to you, holds nothing but a reading position in public-domain books, and is reset periodically. There is nothing to delete, and nothing identifying you is stored.

## What is kept, and for how long

While an account exists, the server stores the account itself, reading positions and bookmarks, and for administrators an audit log of sign-ins and administrative actions. Deleting the account removes the first two immediately. Audit rows are retained for the period the operator has configured and are then removed automatically.

Nothing is retained anywhere else: the app sends no analytics, and there is no developer-side copy of a server's data. The one exception is the dictionary lookup described in the [privacy policy](privacy.md), which sends only the word you look up and stores nothing.

## Questions

About the app itself: `support@tandembook.com`. About a specific server and the data on it: that server's operator.
