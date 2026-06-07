#!/usr/bin/env python3
"""
Audit ebook files for DRM / readability.

Walks a directory of ebooks and flags files that are DRM-encrypted or otherwise
unreadable, so they can be re-imported. Mirrors scripts.audit_audio_integrity.

By default it runs only the *fast* checks (zip validity + DRM detection via
META-INF/encryption.xml), which is appropriate for large libraries (~16k epubs).
Pass --deep to also fully parse each book and confirm it yields usable text
(slower, reuses services.ebook_integrity.check_ebook_integrity).

    docker exec book-sync-server-1 python -m scripts.audit_ebook_integrity /data/ebooks
    docker exec book-sync-server-1 python -m scripts.audit_ebook_integrity /data/ebooks --deep

Exit code is 0 when every scanned file passes, 1 when any file fails.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.ebook_integrity import (  # noqa: E402
    _epub_is_drm_encrypted,
    check_ebook_integrity,
)
import zipfile  # noqa: E402

EBOOK_EXTS = (".epub", ".mobi", ".azw3", ".azw")


def _fast_check(path: str):
    """Cheap check: zip validity + DRM manifest (EPUB only). Returns (ok, detail)."""
    if path.lower().endswith(".epub"):
        try:
            with zipfile.ZipFile(path):
                pass
        except zipfile.BadZipFile:
            return False, "not a valid zip (corrupt) — re-import required"
        if _epub_is_drm_encrypted(path):
            return False, "DRM-encrypted (Adobe ADEPT) — re-import a DRM-free copy"
    return True, "ok (fast check)"


def iter_ebooks(root: str):
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.lower().endswith(EBOOK_EXTS):
                yield os.path.join(dirpath, name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", help="Directory to scan (e.g. /data/ebooks)")
    ap.add_argument("--deep", action="store_true",
                    help="Also fully parse each book (slower, catches non-DRM corruption)")
    ap.add_argument("--quiet", action="store_true", help="Only print failures")
    args = ap.parse_args()

    check = check_ebook_integrity if args.deep else _fast_check

    total = 0
    failures = []
    for path in sorted(iter_ebooks(args.root)):
        total += 1
        ok, detail = check(path)
        if ok:
            if not args.quiet:
                print(f"PASS\t{path}\t{detail}")
        else:
            failures.append((path, detail))
            print(f"FAIL\t{path}\t{detail}", flush=True)

    print(
        f"\nScanned {total} ebook(s): {total - len(failures)} passed, "
        f"{len(failures)} failed.",
        file=sys.stderr,
    )
    if failures:
        print("\nEbooks needing re-import:", file=sys.stderr)
        for path, detail in failures:
            print(f"  {path}  ({detail})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
