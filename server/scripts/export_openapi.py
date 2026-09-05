#!/usr/bin/env python
"""
Export the API schema to `docs/openapi.json` (issue #260).

The interactive Swagger UI is dev-only (issue #263, `main.docs_urls`), so the
committed export is the API reference everyone else reads — a contributor, a
third-party client author, or anyone diffing a release. `docs/api.md` is the
prose companion; this file is the machine-readable half.

    python server/scripts/export_openapi.py            # regenerate
    python server/scripts/export_openapi.py --check     # fail if it has drifted

CI runs the regenerate form and then `git diff --exit-code docs/openapi.json`,
the same drift-gate shape as `alembic check`;
`server/tests/test_openapi_export.py` pins the same thing locally.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SERVER_DIR.parent
OUTPUT_PATH = _REPO_ROOT / "docs" / "openapi.json"

# Environment prep for the *script* case only. Importing `main` reads
# `config.Settings` at module scope and creates `$APP_DATA_DIR/logs`, which on a
# developer machine or a CI runner is `/data/app` and not writable. When this
# module is imported from the test suite, conftest has already configured the
# environment (and imported `config`), so leave it alone.
if "config" not in sys.modules:
    os.environ.setdefault("APP_ENV", "dev")
    os.environ.setdefault(
        "APP_DATA_DIR", tempfile.mkdtemp(prefix="tandem-openapi-export-")
    )
    if str(_SERVER_DIR) not in sys.path:
        sys.path.insert(0, str(_SERVER_DIR))


def build_spec() -> dict:
    """The live app's OpenAPI document.

    `app.openapi()` builds the schema regardless of whether `openapi_url` is
    served, so this works in either environment — but the export is defined as
    the dev-mode document, because that is the one a reader can compare against
    their own `/docs`.
    """
    import main

    return main.app.openapi()


def render(spec: dict) -> str:
    """Bytes-stable rendering: sorted keys, 2-space indent, trailing newline.

    Stability is the whole point — an unstable dump would make the CI drift gate
    fire on every unrelated commit.
    """
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def committed() -> str:
    return OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else ""


def main_cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if docs/openapi.json is out of date; write nothing",
    )
    args = parser.parse_args(argv)

    current = render(build_spec())

    if args.check:
        if committed() == current:
            print(f"{OUTPUT_PATH} is up to date")
            return 0
        print(
            f"{OUTPUT_PATH} is out of date. Regenerate it with:\n"
            f"    python server/scripts/export_openapi.py",
            file=sys.stderr,
        )
        return 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # newline="" — no platform translation. The bytes must be identical whether
    # the export is regenerated on Windows or on the Linux CI runner, or the
    # drift gate fires on line endings alone.
    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="") as fh:
        fh.write(current)
    print(f"wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main_cli())
