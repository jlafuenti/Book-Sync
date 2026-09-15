"""forget the ebook integrity failures caused by extension-only content detection (issue #561)

The EPUB zip walk decided whether a spine item was a content document by its
file extension (`.xhtml`, `.html`, `.htm`). EPUB says so through the manifest
`media-type`, and publisher tooling commonly names XHTML files `chapter01.xml`.
Every such document was read as empty, so a book built that way extracted no
text, `check_ebook_integrity` failed it as "ebook produced almost no text —
likely encrypted or corrupt", and Troubleshoot listed it under DRM.

0019 already dropped the rows with that message, but a Library verify run after
0.2.1 re-checked those books with a parser that still had this bug and cached
the same false failure again. `library_check_results` is reused while a file's
size and mtime are unchanged, so those rows would outlive this fix: the file
never changes. This drops exactly the rows with that message, as 0019 did. The
next Library verify re-checks those files with the fixed parser. A book that
genuinely has no text fails again with the same message; real DRM and
corrupt-zip rows are untouched.

Data only, no schema change. Downgrade is a no-op: the deleted rows are a cache.

Revision ID: 0020_epub_xml_content_docs
Revises: 0019_ebook_integrity_href_fix
Create Date: 2026-09-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0020_epub_xml_content_docs"
down_revision: Union[str, None] = "0019_ebook_integrity_href_fix"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "DELETE FROM library_check_results "
            "WHERE check_type = 'ebook_integrity' AND ok = :failed "
            "AND detail LIKE 'ebook produced almost no text%'"
        ),
        {"failed": False},
    )


def downgrade() -> None:
    pass
