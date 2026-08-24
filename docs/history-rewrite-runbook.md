# History rewrite runbook (issue #153)

The pre-publication PRs removed the tracked SQLite databases and other stray
files from `HEAD`, but they remain in every historical commit. Before the repo
is flipped public, the history itself must be rewritten so those blobs (and the
LAN IP baked into an old `network_security_config.xml`) are gone from every
ref.

**Run this once, after all pre-publication PRs are merged and immediately
before making the repository public.** Do not run it while PRs are still open —
the rewrite changes every commit SHA and will orphan them.

## Prerequisites

1. Install [git-filter-repo](https://github.com/newren/git-filter-repo)
   (`pip install git-filter-repo` or the distro package).
2. Work on a **fresh mirror clone**, never your working checkout:

   ```bash
   git clone --mirror git@github.com:jlafuenti/Book-Sync.git book-sync-rewrite
   cd book-sync-rewrite
   ```

## The rewrite

```bash
git filter-repo --invert-paths \
  --path android/booksync.db \
  --path server/booksync.db \
  --path android/local.properties \
  --path android/.gradle/ \
  --path-glob 'android/build/*' \
  --path .playwright-mcp/ \
  --path scripts/backup.sh
```

Note: `.playwright-mcp/` and `scripts/backup.sh` were audited and contain
nothing sensitive — they are included only to shrink the pack.

### Optional: redact the historical LAN IP

The old `android/.../network_security_config.xml` carried the LAN IP
`REDACTED`. To scrub it from history, create a replacements file:

```bash
echo 'REDACTED==>REDACTED' > /tmp/replacements.txt
git filter-repo --replace-text /tmp/replacements.txt
```

### Optional: normalize author emails

To rewrite commits onto the GitHub noreply address, create a mailmap and pass
it to filter-repo:

```bash
cat > /tmp/mailmap <<'EOF'
Jesse <jlafuenti@users.noreply.github.com> <jlafuenti@gmail.com>
EOF
git filter-repo --mailmap /tmp/mailmap
```

## Push

```bash
git push --force --mirror origin
```

## Consequences — read before pushing

- **Every commit SHA changes.** All existing clones and worktrees are orphaned;
  re-clone everywhere (including the Docker host's checkout).
- **PR commit links break.** GitHub PR pages will reference SHAs that no longer
  resolve.
- **GitHub may cache old objects.** Force-pushed-away blobs can remain
  reachable via cached views and API for a while; contact GitHub Support and
  ask them to run garbage collection / purge cached objects if that matters.

## Post-rewrite verification

From the rewritten mirror (or a fresh clone of it):

```bash
# No secrets anywhere in any ref
gitleaks detect --source . --log-opts="--all"

# No database file was ever added in the surviving history — must print nothing
git log --all --diff-filter=A --name-only | grep -iE '\.(db|sqlite)$'
```

Both must come back clean before flipping the repository public.
