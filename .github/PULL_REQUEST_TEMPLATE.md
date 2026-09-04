## What & why

<!-- Briefly: what does this change do, and why? Link the issue (e.g. Closes #46). -->

## Testing

<!-- How did you verify this? Commands run, cases covered. -->

### Checklist
- [ ] Added or updated tests for the new/changed behavior (test-first where practical)
- [ ] Full suite passes locally (`cd server && ./setup-testenv.sh && .venv/Scripts/python.exe -m pytest -q`
      — never a global `python`; see [docs/testing.md](../docs/testing.md))
- [ ] Patch coverage ≥ 80% on changed lines (the CI `diff-cover` gate) — or the change is in
      an intentionally-manual module (see the exclude list in [docs/testing.md](../docs/testing.md))
- [ ] No new deprecation warnings introduced (warnings are surfaced in the test output)
