# Releasing to Google Play

The route to a public listing for Tandem, and the dates that route implies.
Issue #173: nothing in this repository owned the release process, so an
"imminent" launch quietly assumed it could go straight to production. It cannot.

## The account type decides everything

Google requires a **closed test before production access** for *personal*
developer accounts created after **13 November 2023**. Organisation accounts and
older personal accounts are exempt and can publish to production directly.

**Tandem's account is a new personal account, so the requirement applies.**

What it means, from the policy
([support.google.com/googleplay/android-developer/answer/14151465](https://support.google.com/googleplay/android-developer/answer/14151465)):

- run a **closed test**;
- with at least **12 testers opted in** (the requirement was 20 originally and
  was reduced to 12 — confirm the current number in Play Console when you file,
  it is shown there);
- **continuously for 14 days** — the count must stay at or above the minimum for
  the whole stretch, not just touch it once;
- then **apply for production access**, and wait for Google's review.

**"Opted in" is stricter than "invited".** A tester counts only once they have
accepted the invitation *and* installed the build from Play under the same Google
account the invitation was sent to. A friend who says yes and never installs is
not a tester; nor is one who installs a sideloaded APK; nor is one who accepts on
an account other than the one you listed. Uninstalling drops them back out and
can break the continuous stretch.

## Testers need somewhere to sign in

The twelve testers hit exactly the wall a Play reviewer hits: the app is a client
for a server they do not have (issues #147, #175). A tester who cannot sign in
does not use the app, does not stay opted in, and gives you nothing to show for
the fourteen days.

So **the public demo server is a prerequisite for the closed test**, not just for
the App access form. Stand it up first — [demo-server.md](demo-server.md) — and
build the closed-test artifact with the demo settings so testers get the **Try the
demo** button rather than an empty address field.

Tell testers, in the closed-test description:

- the app needs a Tandem server; the demo button is there if they have none;
- they must keep it installed for the whole test — an uninstall costs a tester;
- what you actually want feedback on.

Recruit more than twelve. Twelve is the floor, and the count is checked
continuously.

## Timeline

Counting from the day the twelfth tester is opted in, not from the upload:

| | Elapsed |
|---|---|
| Recruit testers, they accept and install | as long as it takes — start early |
| 14 continuous days at ≥12 opted-in testers | day 0 → day 14 |
| Apply for production access | day 14 |
| Google reviews the application (~7 days, sometimes longer) | day 14 → ~day 21 |
| First production release submitted, app review | +hours to a few days |

**Roughly three weeks after the testers are in place, and none of it can be
compressed.** The clock is wall-clock time, so it runs in parallel with the rest
of the pre-launch work — which is the argument for starting the closed test with
whatever build exists now rather than waiting for a perfect one. `versionCode`
is still 1 (`android/app/build.gradle.kts`), so nothing has been uploaded and the
clock has not started.

## Record it here

Fill this in as it happens; it is the answer to "when can we ship" and to
"why is this taking so long".

| Fact | Value |
|---|---|
| Developer account type | Personal, created after 2023-11-13 → closed test required |
| Minimum testers at filing time | *(confirm in Play Console — 12 at last check)* |
| Closed test first upload | |
| Twelfth tester opted in (day 0) | |
| 14 continuous days completed | |
| Production access applied for | |
| Production access granted | |
| First production release | |

## Before the first submission

Play prerequisites tracked as issues. None of these are release *steps*; they are
things that must be true before a submission is worth making.

- [ ] **#146 — account deletion.** Accounts can be created but never deleted,
      in-app or on the web. Play's account-deletion policy requires both an
      in-app route and a web-accessible one. A rejection reason on its own.
- [ ] **#150 — privacy policy and Data safety.** The policy has to exist at a
      public URL and the Data safety form has to match it: the `dictionaryapi.dev`
      lookups from the reader, the device name, and the fact that all other data
      goes to a server the user runs.
- [ ] **#147 — App access.** The demo server, the `playreview` credentials and the
      "enter the server address first" instructions, entered in
      Play Console → App content → App access. Text to paste is in
      [demo-server.md](demo-server.md).
- [ ] **#172 — Android Auto opt-in: decided YES.** Opting in adds the car app
      quality checklist to every submission, and a failure there blocks the whole
      release rather than only the Auto feature. Since the answer is yes, walk the
      media-app section of the checklist on the Desktop Head Unit before the first
      submission — no auto-play on connect, browse content inside ~10 s, a clear
      error state when the server is unreachable, playback resumption — and settle
      voice search (`onSearch` is not implemented and `MEDIA_PLAY_FROM_SEARCH` has
      no handler; either implement them or remove the intent filter).
- [ ] Signed artifact. `bundleRelease` with the upload keystore configured in
      `android/local.properties` — [android.md](android.md), "Release builds and
      signing".
- [ ] Listing text. [play-listing.md](play-listing.md); the "requires a
      self-hosted Tandem server" line comes first in every field.
- [ ] `targetSdk` at Play's current floor. Pinned by `BuildConfigPinsTest`; the
      floor rises one API level a year.

## Policy pages

Read these rather than this file when the two disagree — Google changes them.

- Closed testing and production access for personal developer accounts:
  https://support.google.com/googleplay/android-developer/answer/14151465
- App access / sign-in details for gated apps:
  https://support.google.com/googleplay/android-developer/answer/15748846
- Providing credentials for restricted functionality:
  https://support.google.com/googleplay/android-developer/answer/9859455
- Target API level requirements:
  https://developer.android.com/google/play/requirements/target-sdk
- Car app quality (only while opted in to Auto):
  https://developer.android.com/docs/quality-guidelines/car-app-quality
