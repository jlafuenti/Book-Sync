<!-- First PR here? Read CONTRIBUTING.md — the CI gates (patch coverage, alembic
     drift, sync parity) fail rather than being raised at review:
     https://github.com/jlafuenti/Book-Sync/blob/main/CONTRIBUTING.md
     Security problems go through SECURITY.md, never a public PR or issue. -->

## What & why

<!-- Briefly: what does this change do, and why? Link the issue (e.g. Closes #46). -->

## Testing

<!-- How did you verify this? Commands run, cases covered. -->

### Checklist
- [ ] Added or updated tests for the new/changed behavior (test-first where practical)
- [ ] Full suite passes locally (`cd server && pytest`)
- [ ] Patch coverage ≥ 80% on changed lines (the CI `diff-cover` gate) — or the change is in
      an intentionally-manual module (see the exclude list in [docs/testing.md](../docs/testing.md))
- [ ] No new deprecation warnings introduced (warnings are surfaced in the test output)
