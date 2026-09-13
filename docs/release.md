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
the App access form. Stand it up first, and
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
whatever build exists now rather than waiting for a perfect one. Nothing
has been uploaded to Play yet, so the clock has not started. (`versionCode` became
100 at the 0.1.0 release under the scheme in `docs/releasing.md`, so its value is no
longer the way to tell — the Play Console is.)

## Record it here

Fill this in as it happens; it is the answer to "when can we ship" and to
"why is this taking so long".

| Fact | Value |
|---|---|
| Developer account type | Personal, created **2026-09-05** (developer name "Tandem Book") → closed test required |
| Identity verification | Submitted 2026-09-05, awaiting Google |
| Where testers sign in | The public demo stack (#147): second compose project on the owner's docker host behind a Cloudflare Tunnel, kept on the latest `main` daily; built once the repo is public |
| Android Auto | Opting in at first submission (#172); head-unit pass pending |
| Minimum testers at filing time | *(confirm in Play Console — 12 at last check)* |
| Closed test first upload | |
| Twelfth tester opted in (day 0) | |
| 14 continuous days completed | |
| Production access applied for | |
| Production access granted | |
| First production release | |

## Before the first submission

Play prerequisites tracked as issues. None of these are release *steps*; they are
things that must be true before a submission is worth making. Verified 2026-09-13.

- [x] **#146 — account deletion.** In-app (Account → Danger zone) and on the web
      (`/account-deletion`, also published on the public site). Play's account-deletion
      policy requires both routes.
- [x] **#150 — privacy policy and Data safety.** The policy is live on the public site
      (with the terms and the account-deletion page); the Data safety answers that
      match it are in [play-listing.md](play-listing.md), still to be entered in the
      Console.
- [ ] **#147 — App access.** The demo server is live and kept current from `main`.
      Still to do: enter its URL, the demo account and the "enter the server address
      first" instructions in Play Console → App content → App access, and run the
      issue's outside-LAN acceptance test with a *signed* build.
- [ ] **#172 — Android Auto opt-in: decided YES.** Opting in adds the car app
      quality checklist to every submission, and a failure there blocks the whole
      release rather than only the Auto feature. Voice search is implemented
      (`AutoSearch`, the session's `onSearch`, and the `MEDIA_PLAY_FROM_SEARCH`
      handler), so the manifest no longer advertises anything it cannot do. What
      remains is the twelve-row walk-through in [android.md](android.md), "Desktop
      Head Unit walk-through", on the phone with the head-unit server on; record the
      result in the table above.
- [ ] Signed artifact. CI builds an **unsigned** `bundleRelease` on every PR, so the
      R8 path is known good; a signed one needs the upload keystore, created once
      outside the checkout and backed up off the machine, with its four properties
      in `android/local.properties` — [android.md](android.md), "Release builds and
      signing". Enrol in **Play App Signing** when the app is created in the Console,
      so Google holds the app signing key and the upload key can be rotated. Keep
      `mapping.txt` from every uploaded build.
- [x] Listing text. [play-listing.md](play-listing.md); the "requires a
      self-hosted Tandem server" line comes first in every field. Screenshots and
      the feature graphic are still to be taken.
- [x] `targetSdk` at Play's current floor. 36, pinned by `BuildConfigPinsTest`; the
      floor rises one API level a year (36 is required from August 2026).

**Order that respects the clock:** keystore → first closed-test upload (this starts
nothing by itself, but the twelfth opted-in tester does, so upload early) → Console
forms and the head-unit walk in parallel → outside-LAN demo test with that build →
day 14 → production access.

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
