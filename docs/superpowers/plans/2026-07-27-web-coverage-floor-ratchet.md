# Web coverage floor + ratchet (issue #83)

Mirror the server's floor+ratchet coverage policy on the web side. Today `web/vite.config.js`
has no `coverage.thresholds`, so the only web gate is the PR patch-coverage check
(diff-cover ≥80% on changed lines). That stops new untested code but does nothing about
backsliding — deleting tests or adding large untested files passes CI.

## Current state (measured 2026-07-27)

`cd web && npm run coverage` on this branch:

```
Test Files  19 passed (19)
Tests      159 passed (159)
All files  |  31 (stmts) | 67.47 (branch) | 31.32 (funcs) | 31 (lines)
```

(The issue text says 12 files / 63 tests — stale; the suite has grown since it was filed.)

Existing gates:
- `.github/workflows/web-tests.yml` runs `npm run coverage` on every push to any branch and on
  PRs touching `web/**`, then diff-cover `--fail-under=80` on PRs only.
- Because CI runs `npm run coverage` on **push** as well, adding vitest thresholds to the
  config automatically gives us a push-time global floor with no workflow change needed —
  vitest exits non-zero when a threshold is unmet.

## Decisions

1. **Floor = total − 3**, whole percent, matching the server rule in `docs/testing.md`
   ("bump `--cov-fail-under` to `new_total − 3` after any PR that raises it"). That gives:
   - `lines: 28`, `statements: 28`, `functions: 28`, `branches: 64`
2. **Set all four metrics**, not just lines. Branches sits far above lines (67 vs 31) because
   the tested files are branch-dense; a floor of 64 there is the same −3 rule and catches a
   class of regression (adding untested conditionals to already-covered files) that a lines
   floor alone would miss.
3. **No workflow YAML change.** The gate rides on `npm run coverage`, which CI already runs.
   Only the two stale comments (config + workflow) that say "no global floor yet" get updated.
4. **Milestone targets 30 → 40 → 50**, same ladder as the server, documented in testing.md.

## Steps

### 1. Prove the gate bites (TDD for a config change)

Before setting the real numbers, verify the mechanism actually fails the build:

- Temporarily set `thresholds.lines: 95` in `web/vite.config.js`, run
  `cd web && npm run coverage`, and confirm a non-zero exit with an
  `ERROR: Coverage for lines (31%) does not meet global threshold (95%)` message.
- This is the "red" — it demonstrates the floor is enforced rather than decorative. Record the
  output in the PR description.

### 2. Add the real thresholds

In `web/vite.config.js`, replace the "No global floor yet" comment block inside
`test.coverage` with:

```js
// Global floor (anti-backslide), mirroring the server policy in docs/testing.md:
// floor = total − 3. Actual totals at the time of writing: 31/67/31/31.
// Raise these after any PR that increases the total. The other web gate is PATCH
// coverage (diff-cover ≥80% on changed lines, PRs only, in web-tests.yml).
thresholds: {
    lines: 28,
    statements: 28,
    functions: 28,
    branches: 64,
},
```

Re-run `npm run coverage` — must pass (green).

### 3. Update the stale CI comment

`.github/workflows/web-tests.yml`, the `Check patch coverage (PRs)` step comment currently
ends with "There is no global floor yet — web coverage is ~0 until the pages get tests; add
one once it's non-trivial." Replace with a note that the global floor is enforced by vitest
thresholds inside `npm run coverage` (the step above), so both gates are visible from the
workflow file.

### 4. Document the web ratchet in `docs/testing.md`

- In the **Web (`web/`)** section (~line 168), replace the "There is no global floor yet…"
  sentence with the two-gate description: global floor via `coverage.thresholds` in
  `web/vite.config.js` (currently 28/28/28/64), plus ≥80% patch coverage via diff-cover on PRs.
- In **The ratchet** section (~line 130), add a **Web** paragraph: after any PR that raises the
  web total, bump each threshold to `metric_total − 3`; milestones 30 → 40 → 50. Note the
  highest-leverage backfill targets from the current report — the pages that are near-zero and
  large: `LibraryPage.jsx` (6%), `PairsPage.jsx` (1.4%), `TranscriptionPage.jsx` (1.1%),
  `ImportSourcesPage.jsx` (0.15%), `NewPairsPage.jsx` (4%), `UserManagementPage.jsx` (6.7%).

### 5. Verify

- `cd web && npm run coverage` passes locally.
- Confirm the threshold numbers in the config, testing.md, and the workflow comment agree.
- Push the branch and confirm the Web tests workflow is green.

## Out of scope

- Writing new web tests to raise coverage. This PR sets a floor at today's level; raising it is
  the ratchet's job.
- The process problem the issue notes (6 recent PRs needing follow-up "satisfy the gate"
  commits). That is addressed by the TDD rule already in `CLAUDE.md`, not by a threshold.

## Risks (both mitigated)

- ~~The floor is measured on this worktree's branch; if `main` has fewer web tests, 28 could be
  above main's actual total.~~ **Resolved:** `git diff origin/main...HEAD -- web/` is empty and
  there are no untracked files under `web/`, so the measured 31/67.47/31.32/31 *is* main's
  total. No second run needed.
- ~~Vitest applies thresholds per-run, so a filtered run fails the floor spuriously.~~
  **Mitigated by documentation:** called out in the `vite.config.js` comment and in the Web
  section of `docs/testing.md`, which points contributors at `npm test` for the red-green loop
  and reserves `npm run coverage` as the gate.
