"""Open an MP4-family audio file for its tags even when mutagen cannot decode
its legacy Nero `chpl` chapter titles (issue #935).

mutagen parses the chapter atom while it opens the file, and a title that is
not valid UTF-8 makes `MP4Chapters._parse_chpl` raise ``chapter N title: ...``,
which aborts the whole open: no tags to read, no tags to write. The tag
readers and writers do not need the chapter list, and `MP4Tags.save` rewrites
only `ilst` and the parent atom sizes, so the `chpl` bytes stay where they
are. This module therefore parses *around* the bad atom.

The file is deliberately not repaired here. A purchased media file is never
edited unless the operator clicks Repair in Troubleshoot
(`services.chapter_repair`). That module keeps using the strict parser in
`check_chapter_encoding`, so Troubleshoot still lists these files.
"""

import logging

import mutagen
import mutagen.mp4

from services.chapter_repair import CHAPTER_TITLE_ERROR_RE

logger = logging.getLogger(__name__)


class _TolerantChapters(mutagen.mp4.MP4Chapters):
    def load(self, atoms, fileobj):
        try:
            super().load(atoms, fileobj)
        except Exception as e:
            logger.warning("[mp4] Unreadable chpl chapter list, chapters ignored: %s", e)
            self._chapters = []


class MP4(mutagen.mp4.MP4):
    """mutagen.mp4.MP4 with the tolerant chapter loader. Named `MP4` on purpose:
    the writers and the extractor dispatch on `type(audio).__name__`."""

    MP4Chapters = _TolerantChapters


def open_audio(filepath):
    """`mutagen.File`, but an MP4-family file whose only fault is a chapter
    title mutagen cannot decode is reopened with its chapters ignored. Any
    other error propagates as before."""
    try:
        return mutagen.File(filepath)
    except mutagen.mp4.MP4MetadataError as e:
        if CHAPTER_TITLE_ERROR_RE.search(str(e)):
            logger.warning("[mp4] %s: %s; reopening without chapters", filepath, e)
            return MP4(filepath)
        raise
