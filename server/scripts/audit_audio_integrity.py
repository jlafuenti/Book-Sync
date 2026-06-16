#!/usr/bin/env python3
"""
Audit audiobook files for decode integrity.

Walks a directory of audiobooks and runs the same full-decode validation used by
the transcription queue gate (services.audio_integrity.check_audio_integrity),
reporting any files that are truncated or have corrupt media streams so they can
be re-imported.

Intended to be run inside the book-sync server container, where the audiobook
library is mounted and ffmpeg/ffprobe are available, e.g.:

    docker exec book-sync-server-1 python -m scripts.audit_audio_integrity \
        /data/audiobooks --since 2026-06-06 --until 2026-06-07

Exit code is 0 when every scanned file passes, 1 when any file fails (handy for
scripting / CI).
"""

import argparse
import datetime as _dt
import os
import sys

# Allow running both as `python -m scripts.audit_audio_integrity` (from the
# server dir) and as a direct script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.audio_integrity import check_audio_integrity  # noqa: E402

AUDIO_EXTS = (".m4b", ".m4a", ".mp4", ".mp3", ".aax", ".flac", ".ogg")


def _parse_date(s: str) -> float:
    return _dt.datetime.strptime(s, "%Y-%m-%d").timestamp()


def iter_audio_files(root: str, since: float | None, until: float | None):
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.lower().endswith(AUDIO_EXTS):
                continue
            full = os.path.join(dirpath, name)
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            if since is not None and mtime < since:
                continue
            if until is not None and mtime >= until:
                continue
            yield full


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", help="Directory to scan (e.g. /data/audiobooks)")
    ap.add_argument("--since", help="Only files modified on/after YYYY-MM-DD")
    ap.add_argument("--until", help="Only files modified before YYYY-MM-DD")
    ap.add_argument("--quiet", action="store_true", help="Only print failures")
    args = ap.parse_args()

    since = _parse_date(args.since) if args.since else None
    until = _parse_date(args.until) if args.until else None

    total = 0
    failures = []
    for path in sorted(iter_audio_files(args.root, since, until)):
        total += 1
        ok, detail = check_audio_integrity(path)
        if ok:
            if not args.quiet:
                print(f"PASS\t{path}\t{detail}")
        else:
            failures.append((path, detail))
            print(f"FAIL\t{path}\t{detail}", flush=True)

    print(
        f"\nScanned {total} file(s): {total - len(failures)} passed, "
        f"{len(failures)} failed.",
        file=sys.stderr,
    )
    if failures:
        print("\nFiles needing re-import:", file=sys.stderr)
        for path, detail in failures:
            print(f"  {path}  ({detail})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
