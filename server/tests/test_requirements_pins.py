"""Dependency floor pins (issue #151).

The installed environment mirrors CI (setup-testenv.sh), so asserting on
importlib.metadata pins both requirements.txt and the resolved transitive
versions.
"""

import importlib.metadata
import re


def _version_tuple(dist: str) -> tuple:
    """Parse an installed distribution version into an int tuple.

    Tolerates non-numeric suffixes (e.g. "0.0.31.post1", "1.2rc1") by taking
    the leading digits of each dot-separated component and stopping at the
    first component with none.
    """
    parts = []
    for component in importlib.metadata.version(dist).split("."):
        m = re.match(r"\d+", component)
        if not m:
            break
        parts.append(int(m.group()))
    return tuple(parts)


def test_starlette_at_least_0_47_2():
    # starlette 0.38.x is vulnerable to CVE-2024-47874 (multipart/form-data
    # DoS via unbounded memory buffering) and CVE-2025-54121 (blocking main
    # thread while spooling large multipart files). Fixed by 0.47.2.
    assert _version_tuple("starlette") >= (0, 47, 2)


def test_python_multipart_at_least_0_0_31():
    # python-multipart < 0.0.18 has multipart parsing DoS CVEs
    # (GHSA-2jv5-9r88-3w3p, GHSA-59g5-xgcq-4qw3); 0.0.31 is the floor we pin.
    assert _version_tuple("python-multipart") >= (0, 0, 31)
