## What & why

<!-- Briefly: what does this change do, and why? Link the issue (e.g. Closes #46). -->

## Testing

<!-- How did you verify this? Commands run, cases covered. -->

### Checklist
- [ ] Added or updated tests for the new/changed behavior (test-first where practical)
- [ ] Full suite passes locally (`cd server && .venv/Scripts/python.exe -m pytest` — set the venv up
      once with `./setup-testenv.sh`; never a global `python`, see [docs/testing.md](../docs/testing.md))
- [ ] Notable user-facing change recorded under `Unreleased` in [CHANGELOG.md](../CHANGELOG.md)
- [ ] Patch coverage ≥ 80% on changed lines (the CI `diff-cover` gate) — or the change is in
      an intentionally-manual module (see the exclude list in [docs/testing.md](../docs/testing.md))
- [ ] No new deprecation warnings introduced (warnings are surfaced in the test output)
