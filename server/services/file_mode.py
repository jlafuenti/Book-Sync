"""
File modes for library files (issue #885).

`tempfile.mkstemp` always creates a file as ``0600``, and that mode travels with
the file when it replaces a library file or is placed into the library. Other
programs sharing the library (the *arr apps, other readers, an SMB/NFS export)
often read it as a different user, so the file became unreadable to them the
moment Tandem touched it.
"""

import os
import stat
import tempfile
import uuid


def copy_mode_and_owner(original: str, staged: str) -> None:
    """Give `staged`, about to replace `original`, the original's mode and owner.

    The owner is best effort: only root can give a file away, and a server not
    running as root still needs the write to succeed.
    """
    info = os.stat(original)
    os.chmod(staged, stat.S_IMODE(info.st_mode))
    if hasattr(os, "chown"):
        try:
            os.chown(staged, info.st_uid, info.st_gid)
        except OSError:
            pass


def apply_default_mode(path: str) -> None:
    """Give a file new to the library the mode a plainly created file would get."""
    os.chmod(path, _default_mode())


def _default_mode() -> int:
    # Creating a file is the one way to read the umask without setting it, and
    # `os.umask` would change it for every thread for a moment.
    probe = os.path.join(tempfile.gettempdir(), f".tandem-mode-{uuid.uuid4().hex}")
    fd = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
    try:
        return stat.S_IMODE(os.fstat(fd).st_mode)
    finally:
        os.close(fd)
        os.unlink(probe)
