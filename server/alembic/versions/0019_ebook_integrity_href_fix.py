"""forget the ebook integrity failures caused by undecoded manifest hrefs (issue #554)

The EPUB zip walk read each manifest href as a literal zip entry name. Hrefs are
URLs, so a content file with a space in its name (`Text/Axis%20Test_1.html`)
was never found, and a book named that way throughout extracted almost no text.
`check_ebook_integrity` then failed it as "ebook produced almost no text —
likely encrypted or corrupt", and Troubleshoot listed it under DRM.

`library_check_results` is a cache reused while a file's size and mtime are
unchanged, so those false failures would outlive the fix: the file never
changes. This drops exactly the rows with that message. The next Library verify
re-checks those files with the fixed parser. A book that genuinely has no text
fails again with the same message; real DRM and corrupt-zip rows are untouched.

Data only, no schema change. Downgrade is a no-op: the deleted rows are a cache.

Revision ID: 0019_ebook_integrity_href_fix
Revises: 0018_queue_pair_status_before
Create Date: 2026-09-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
# NOTE: keep this at or under 32 chars — see the note in
# 0002_conflict_resolution.py (alembic_version is a VARCHAR(32)).
revision: str = "0019_ebook_integrity_href_fix"
down_revision: Union[str, None] = "0018_queue_pair_status_before"
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
