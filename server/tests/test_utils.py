"""Unit tests for the safe_join path-containment helper (issue #49).

safe_join reduces any untrusted filename to its final path segment (discarding
all directory components, whether via `/`, `\\`, or `..`) and then verifies the
joined result resolves inside base_dir. So traversal attempts aren't so much
"rejected" as neutralized down to a plain basename that lands inside base_dir;
only names that are empty, hidden, or literally `.`/`..` are rejected outright.
"""

import pytest
from fastapi import HTTPException

from utils import safe_join


def test_safe_join_accepts_normal_name(tmp_path):
    result = safe_join(tmp_path, "book.epub")
    assert result == tmp_path.resolve() / "book.epub"


def test_safe_join_accepts_unicode_name(tmp_path):
    result = safe_join(tmp_path, "Café_Roman.epub")
    assert result == tmp_path.resolve() / "Café_Roman.epub"


def test_safe_join_neutralizes_dotdot_traversal(tmp_path):
    """A `../../etc/passwd`-style filename is reduced to its basename and
    stays inside base_dir -- it never resolves to a path outside it."""
    result = safe_join(tmp_path, "../../etc/passwd")
    assert result == tmp_path.resolve() / "passwd"
    assert result.parent == tmp_path.resolve()


def test_safe_join_neutralizes_absolute_path(tmp_path):
    result = safe_join(tmp_path, "/etc/passwd")
    assert result == tmp_path.resolve() / "passwd"
    assert result.parent == tmp_path.resolve()


def test_safe_join_neutralizes_windows_style_path(tmp_path):
    """Backslash-separated prefixes are stripped to a basename too, not just
    forward-slash ones -- guards a POSIX deployment against a Windows-style
    traversal attempt regardless of the server OS."""
    result = safe_join(tmp_path, "C:\\Windows\\evil.epub")
    assert result == tmp_path.resolve() / "evil.epub"
    assert result.parent == tmp_path.resolve()


def test_safe_join_rejects_empty_name(tmp_path):
    with pytest.raises(HTTPException) as exc:
        safe_join(tmp_path, "")
    assert exc.value.status_code == 400


def test_safe_join_rejects_name_that_is_only_traversal(tmp_path):
    """A filename made up entirely of `..`/`/` segments reduces to an empty
    or dot-only basename and must be rejected, not silently written somewhere
    under base_dir with an empty name."""
    with pytest.raises(HTTPException) as exc:
        safe_join(tmp_path, "../../")
    assert exc.value.status_code == 400


def test_safe_join_rejects_dot_name(tmp_path):
    with pytest.raises(HTTPException) as exc:
        safe_join(tmp_path, ".")
    assert exc.value.status_code == 400


def test_safe_join_rejects_hidden_name(tmp_path):
    with pytest.raises(HTTPException) as exc:
        safe_join(tmp_path, ".htaccess")
    assert exc.value.status_code == 400
