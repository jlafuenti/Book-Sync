"""forget the ebook integrity failures caused by obfuscated fonts (issue #560)

The DRM check refused any EPUB whose `META-INF/encryption.xml` used the XML-ENC vocabulary. Every
such manifest does, including the ones that only obfuscate embedded fonts with the IDPF or Adobe
algorithm, which is not DRM: the text is plain and every reader opens the book. Calibre writes
those manifests when it converts a book with embedded fonts, so Tandem's own Convert flow produced
EPUBs that `check_ebook_integrity` then failed as "EPUB is DRM-encrypted (Adobe ADEPT)".

`library_check_results` is a cache reused while a file's size and mtime are unchanged, so those
false failures would outlive the fix. This drops every cached DRM failure. The next Library verify
re-checks those files with the fixed rule; a book that really is encrypted fails again with the
same message. Corrupt-zip and no-text rows are untouched.

Data only, no schema change. Downgrade is a no-op: the deleted rows are a cache.

Revision ID: 0021_epub_font_obfuscation
Revises: 0020_epub_xml_content_docs
Create Date: 2026-09-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0021_epub_font_obfuscation"
down_revision: Union[str, None] = "0020_epub_xml_content_docs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "DELETE FROM library_check_results "
            "WHERE check_type = 'ebook_integrity' AND ok = :failed "
            "AND detail LIKE 'EPUB is DRM-encrypted%'"
        ),
        {"failed": False},
    )


def downgrade() -> None:
    pass
