"""
Composite file-content hashing (issue #45).

The previous scheme hashed only the first 10 MiB of a file, so two different
audiobooks sharing an identical opening (publisher bumper, narrator preamble)
collided: false duplicates in troubleshoot and false hits in the auto-pair
unpair-exclusion memory. The composite scheme hashes SHA-256 over
``str(size) + head + tail``, which stays cheap on multi-GB files (at most
2×CHUNK bytes read) while distinguishing files that differ anywhere outside
a shared intro, or differ only in length.

Every site that stores a comparable ``file_hash`` must go through this module
(scan/upload ingest via ``routers.library.compute_file_hash``, replace via
``routers.troubleshoot``) so hashes stay comparable. Existing rows carrying
old-scheme hashes are migrated by ``POST /api/library/rehash``.
"""

import hashlib
import os

# Head/tail window. A module constant (read at call time) so tests can shrink
# it and exercise the head/tail/size semantics without 10 MiB fixtures.
CHUNK = 10 * 1024 * 1024


def hash_bytes(content: bytes) -> str:
    """Composite hash of in-memory content. Must match hash_file exactly."""
    sha = hashlib.sha256()
    sha.update(str(len(content)).encode())
    sha.update(content[:CHUNK])
    # For content shorter than CHUNK the tail overlaps the head — that's fine,
    # it's deterministic and hash_file reproduces it byte for byte.
    sha.update(content[-CHUNK:])
    return sha.hexdigest()


def hash_file(filepath: str) -> str:
    """Composite hash of a file on disk, reading at most 2×CHUNK bytes."""
    size = os.path.getsize(filepath)
    sha = hashlib.sha256()
    sha.update(str(size).encode())
    with open(filepath, "rb") as f:
        sha.update(f.read(CHUNK))
        f.seek(max(0, size - CHUNK))
        sha.update(f.read(CHUNK))
    return sha.hexdigest()
