# Cutting a release

How a Tandem version gets made, deployed, and undone. Issue #186: with no tags, no changelog and
three hand-written version literals, a bug reporter could not say what they were running, the
operator could not say what was deployed, and there was no named thing to roll back to.

This page covers the repository side. The Google Play route for the Android app is longer, has a
14-day clock in it, and lives in [release.md](release.md) — read that before planning any release
that includes a Play upload.

## The version number

One number covers the whole repo. It is written out in **three** places, by hand:

| File | Field |
|---|---|
| `server/version.py` | `APP_VERSION` |
| `web/package.json` | `version` |
| `android/app/build.gradle.kts` | `versionName` (and `versionCode`, see below) |

**Why not one file the three read?** Because single-sourcing costs a build step in three
toolchains — a `VERSION` file read at import time by Python, a prebuild script rewriting
`package.json` or injecting `__APP_VERSION__` through Vite, and a Gradle read at configuration
time — and each of those is a place a release can break silently. The failure it prevents is
narrow: three literals *disagreeing*. So the literals stay, and
`server/tests/test_docs_contract.py::test_the_three_version_strings_agree` fails the build the
moment a bump touches two of the three. The cost is remembering three edits; the test is what
remembers for you.

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
`versionCode` is still `1` today, which is how you can tell nothing has ever been uploaded to Play
— the first upload is also the first use of this scheme, and `1 → 100` is a legal step.

## Cutting it

1. **Decide the number.** Semver against the previous tag: breaking API change or a migration an
   operator cannot reverse → major (or minor, pre-1.0); new behaviour → minor; fixes only → patch.
2. **Bump the three files** in the table above, plus `versionCode`.
3. **Update `CHANGELOG.md`.** Rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, add a fresh
   empty `## [Unreleased]` above it, and add an `### Upgrade notes` subsection if the release
   contains an irreversible migration, a removed endpoint, or a minimum app version.
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

   Attach the Android artifacts if this release includes an app build — `app-release.apk` for
   sideloading and, when it is also going to Play, the `.aab`. Build them per
   [android.md](android.md), "Release builds and signing".

   > **The GitHub APK and the Play build are signed with different keys** unless you sign both with
   > the Play upload key. A user who sideloads one cannot update to the other; Android refuses the
   > install with a signature mismatch. Say which is which in the release body.

## Deploying it

Images are built from source on the host (`build: ./server`, `build: ./web` in the compose
template) — there is no registry, so **the git tag is the version identifier**:

```bash
cd /path/to/Book-Sync
git fetch --tags
git checkout v0.1.0
docker compose up -d --build
```

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

- [ ] Three version literals bumped, plus `versionCode`
- [ ] `CHANGELOG.md`: version heading dated, new empty `Unreleased`, upgrade notes if needed
- [ ] Suites pass locally
- [ ] Merged to `main`, tag pushed
- [ ] GitHub Release created, artifacts attached and their signing key stated
- [ ] Manual backup taken on the deployment host, then the tag checked out and rebuilt
- [ ] Deployed version confirmed: `curl -s http://<host>/api/health` reports the new `app_version`
