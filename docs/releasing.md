# Cutting a release

How a Tandem version gets made, deployed, and undone. Issue #186: with no tags, no changelog and
three hand-written version literals, a bug reporter could not say what they were running, the
operator could not say what was deployed, and there was no named thing to roll back to.

This page covers the repository side. The Google Play route for the Android app is longer, has a
14-day clock in it, and lives in [release.md](release.md) — read that before planning any release
that includes a Play upload.

## The version number

One number covers the whole repo. It is written out in **four** places, by hand:

| File | Field |
|---|---|
| `server/version.py` | `APP_VERSION` |
| `web/package.json` | `version` |
| `android/app/build.gradle.kts` | `versionName` (and `versionCode`, see below) |
| `jetson/server.py` | `WORKER_VERSION` — reported by the worker's `/v1/health`; the server compares it with its own and the System page says when the worker is behind |

**Why not one file the three read?** Because single-sourcing costs a build step in three
toolchains — a `VERSION` file read at import time by Python, a prebuild script rewriting
`package.json` or injecting `__APP_VERSION__` through Vite, and a Gradle read at configuration
time — and each of those is a place a release can break silently. The failure it prevents is
narrow: the literals *disagreeing*. So the literals stay, and
`server/tests/test_docs_contract.py::test_the_release_version_strings_agree` fails the build the
moment a bump touches some of the four but not all. The cost is remembering four edits; the test is
what remembers for you.

`API_VERSION`, also in `server/version.py`, is a **different number with a different rule** — the
client-compatibility contract, bumped only for a change a shipped app cannot survive. See the
module docstring there and `docs/operations.md`, "Client support window". Most releases do not
touch it.

### Android `versionCode`

`versionName` is what humans read; `versionCode` is the integer Play orders uploads by, and it can
never go down or repeat. Derive it from the version so it cannot regress:

```
versionCode = MAJOR*10000 + MINOR*100 + PATCH
```

`0.1.0` → **100**; `0.2.0` → 200; `1.2.3` → 10203. Bump both fields in the same edit.
`versionCode` moved from `1` to `100` at the 0.1.0 release, the first use of this scheme, before
anything had been uploaded to Play — `1 → 100` is a legal step because no upload ever used `1`.
Its value no longer tells you whether an upload has happened; the Play Console does.

## Cutting it

1. **Decide the number.** Semver against the previous tag: breaking API change or a migration an
   operator cannot reverse → major (or minor, pre-1.0); new behaviour → minor; fixes only → patch.
2. **Bump the four files** in the table above, plus `versionCode`, then regenerate the
   committed API export — `python server/scripts/export_openapi.py` — because its
   `info.version` is `APP_VERSION` and `tests/test_openapi_export.py` fails on a stale copy.
3. **Update `CHANGELOG.md`.** Rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, add a fresh
   empty `## [Unreleased]` above it, and add an `### Upgrade notes` subsection if the release
   contains an irreversible migration, a removed endpoint, or a minimum app version.

   **Summarise the dependency updates.** The `changelog` CI gate exempts Dependabot PRs on the
   promise that they are recorded here, so this is the step that keeps that promise. List what
   merged since the previous tag (`v0.0.9` below stands for the last release):

   ```bash
   gh pr list --state merged --author app/dependabot --limit 100 --search "merged:>$(git log -1 --format=%cI v0.0.9)" --json number,title
   ```

   and add one `Changed` line naming the notable ones (a major, a runtime library, a base
   image) with the rest as "and N minor/patch updates". Read each PR's body, not its title: a
   grouped PR called "minor and patch" has carried a Kotlin major and a Readium minor.
4. **Run the suites** the change touches — `docs/testing.md` has the commands. At minimum the
   server suite, because the version-agreement test lives there.
5. **Merge to `main`** through a PR like any other change.
6. **Tag the merge commit** and push the tag:

   ```bash
   git checkout main && git pull
   git tag -a v0.1.0 -m "Tandem 0.1.0"
   git push origin v0.1.0
   ```

   Tags are `v` + the version. Never move a tag that has been pushed; if it was wrong, cut the next
   patch version instead.
7. **Create the GitHub Release** against that tag, with the changelog section as the body:

   ```bash
   gh release create v0.1.0 --title "Tandem 0.1.0" --notes-file <(sed -n '/## \[0.1.0\]/,/## \[/p' CHANGELOG.md)
   ```

   **Publishing this Release is what running servers notice.** A server whose operator has
   enabled the update check asks GitHub for `releases/latest` every few hours and, when its
   version is newer than the server's `APP_VERSION`, shows "Tandem X.Y.Z is available" on the
   System page with a link to these notes (issue #463). A tag with no Release is invisible to it,
   and so are drafts and pre-releases — so the notes written here are the ones operators read
   before upgrading.

   **Attach no Android artifacts.** The app is distributed through Google Play only; a GitHub
   Release carries the notes and nothing to install. Decided 2026-09-21, after 0.4.1, 0.4.2 and
   0.5.0 had already shipped without assets while this step still said to attach them — the doc
   and the practice disagreed, and a gap nobody decided on looks like a mistake. The Play upload
   is `./gradlew publishReleaseBundle` from `android/` — [android.md](android.md), "Publishing to
   Play" — and each uploaded build's `.aab`, R8 mapping and release notes are archived outside
   the repository alongside the upload key.

   > **If sideloadable builds are ever offered again**, say in the release body which key signed
   > the APK. A GitHub APK signed with the upload key and the build Play delivers (signed by
   > Google's app-signing key) cannot update one another; Android refuses the install with a
   > signature mismatch.

## Deploying it

Two routes. Either way **the git tag is the version identifier**; the release notes say what
changed and whether a migration is involved.

**Build from source at the tag** (the compose template's `build: ./server`, `build: ./web`):

```bash
cd /path/to/Book-Sync
git fetch --tags
git checkout v0.1.0
docker compose up -d --build
```

**Or pull the versioned image.** Pushing the `v0.1.0` tag makes `publish-images.yml` publish
`ghcr.io/jlafuenti/tandem-server:0.1.0` and `ghcr.io/jlafuenti/tandem-web:0.1.0`, and moves
`:latest` to them. A compose file that names those images (`image:` instead of `build:`, as
`docker-compose.demo.yml` does) upgrades with:

```bash
docker compose pull && docker compose up -d
```

Pin the version tag rather than `latest` on a server you care about; `latest` is for a demo or
a throwaway. The `:main` tag is not a release — it follows every merge and is what the demo
stack tracks through Watchtower. The published image has no local Whisper and no DRM plugins
baked in; a deployment that needs either still builds from source with the build args in
`docs/operations.md`.

The entrypoint runs `alembic upgrade head` before uvicorn, so schema changes apply themselves; a
failed migration stops the boot deliberately. See `docs/operations.md`, "Upgrading".

**Take a manual backup first** — System → Backups — every time, before the checkout. It is the only
thing that makes the last step of a rollback possible, and it takes less time than reading this
sentence twice.

If you want a rollback that does not rebuild, tag the built images while they are fresh:

```bash
docker image tag book-sync-server:latest tandem-server:0.1.0
docker image tag book-sync-web:latest    tandem-web:0.1.0
```

(Compose names images after the project directory, so check `docker image ls` for the real names.)

## Rolling back

**Redeploy the previous tag.** That is the whole procedure when no migration ran:

```bash
git checkout v0.0.9          # the tag you were on
docker compose up -d --build
```

When the release **did** run a migration, the schema is now ahead of the code you just checked
out, and the old server will fail against it. Two routes, in order of preference:

1. **Downgrade the schema.** Find the revision the old code expects — `git show v0.0.9:server/alembic/versions/`
   lists what shipped in it, and `docker compose exec server alembic history` shows the chain —
   then:

   ```bash
   docker compose exec server alembic downgrade <revision>
   ```

   Downgrades are exercised in CI (`tests/test_migrations_postgres.py` runs upgrade → downgrade →
   upgrade against real Postgres), so this path is tested, not hoped for.

2. **Restore the pre-upgrade dump** when the migration is not cleanly reversible — a dropped column
   has no data to give back. Follow [backup-restore.md](backup-restore.md), and note its warning:
   do **not** run `alembic upgrade head` after a `pg_restore`, the dump already carries the schema
   it was taken at.

Either way, note in the incident/issue which tag you rolled back to and why — the next person to
hit it is usually you.

## Checklist

- [ ] Four version literals bumped, plus `versionCode`; `docs/openapi.json` regenerated
- [ ] `CHANGELOG.md`: version heading dated, new empty `Unreleased`, upgrade notes if needed
- [ ] Suites pass locally
- [ ] Merged to `main`, tag pushed
- [ ] GitHub Release created (notes only — no Android artifacts; the app ships through Play)
- [ ] Manual backup taken on the deployment host, then the tag checked out and rebuilt
- [ ] Deployed version confirmed: `curl -s http://<host>/api/health` reports the new `app_version`
