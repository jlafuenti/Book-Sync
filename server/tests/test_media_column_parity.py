"""
`EBook` and `AudioBook` are one model written twice, and the drift gates for it
(issue #257).

The two tables carry the same ~27 columns; only `duration_seconds` is genuinely
audiobook-only. Every metadata feature therefore had to be written twice, and
the list of "metadata fields to copy" was retyped as a string literal in six
places in `routers/library.py` alone. Nothing caught a copy that was missing a
field, and the copies already disagreed: the ebook ingest never copied
`narrators` even though `EBook.narrators` exists.

The columns now come from `models.book.MediaColumnsMixin` and the field list
from `services.metadata_utils`. These tests are the gates that keep it that way:

* the two tables stay column-identical apart from `duration_seconds`;
* the shared field list and the model columns stay in step **in both
  directions** — a column added to the model must be classified, and a name in
  the list must exist as a column;
* the four field groups partition the full list, so a subset can't drift;
* the public JSON key sets of the book responses are pinned literally, because
  the response models are now built from a shared base and a silent widening
  there would change the API.
"""

import pytest

from models.book import AudioBook, EBook
from routers.library import FIELDS_TO_COMPARE, MetadataUpdate
from schemas import AudioBookResponse, EBookResponse
from services.metadata_utils import (
    MEDIA_CORE_FIELDS,
    MEDIA_DESCRIPTIVE_FIELDS,
    MEDIA_EXTRACTED_FIELDS,
    MEDIA_FILL_IF_NULL_FIELDS,
    MEDIA_FLAG_FIELDS,
    MEDIA_IDENTIFIER_FIELDS,
    MEDIA_METADATA_FIELDS,
)

# Columns the two tables share that are *not* book metadata: file identity,
# provenance and inbox bookkeeping. Together with `MEDIA_METADATA_FIELDS` this
# must account for every shared column — that is the "someone added a column"
# gate. Adding a column means adding it to one list or the other, deliberately.
NON_METADATA_COLUMNS = frozenset({
    "id",
    "filename",
    "file_path",
    "file_hash",
    "file_size",
    "format",
    "uploaded_at",
    "metadata_source",
    "metadata_pattern",
    "cover_path",
    "import_source",
    "external_id",
    "acknowledged",
    "auto_pair_excluded_ids",
    "auto_pair_excluded_hashes",
})


def _columns(model):
    return {c.name: c for c in model.__table__.columns}


# ---------- the two tables stay the same table ----------

def test_ebook_and_audiobook_share_common_columns():
    """The symmetric difference is exactly `duration_seconds`.

    This fails the moment someone adds a column to one model and not the other —
    which is how the metadata features drifted apart in the first place.
    """
    ebook_names = set(_columns(EBook))
    audio_names = set(_columns(AudioBook))

    assert ebook_names ^ audio_names == {"duration_seconds"}
    assert "duration_seconds" in audio_names


@pytest.mark.parametrize("name", sorted(set(_columns(EBook)) & set(_columns(AudioBook))))
def test_shared_columns_have_identical_definitions(name):
    """Same type, nullability, primary-key flag and default on both tables.

    The mixin makes this true by construction; the test is what notices if
    someone re-declares one of them on a single class to "just tweak" it.
    """
    ecol = _columns(EBook)[name]
    acol = _columns(AudioBook)[name]

    assert str(ecol.type) == str(acol.type)
    assert ecol.nullable == acol.nullable
    assert ecol.primary_key == acol.primary_key

    def _default(col):
        if col.default is None:
            return None
        return col.default.arg if not col.default.is_callable else col.default.arg.__name__

    # `format` is the one deliberate exception: epub vs mp3.
    if name != "format":
        assert _default(ecol) == _default(acol)


def test_format_default_stays_per_media_type():
    """Pinned because it is the reason `format` is *not* on the mixin."""
    assert _columns(EBook)["format"].default.arg == "epub"
    assert _columns(AudioBook)["format"].default.arg == "mp3"


def test_per_table_indexes_survive_the_mixin():
    """The unique file_path index and the file_hash index are per-table (they
    carry the table name), so they stay on the classes, not the mixin."""
    assert {ix.name for ix in EBook.__table__.indexes} == {
        "ux_ebooks_file_path", "ix_ebooks_file_hash",
    }
    assert {ix.name for ix in AudioBook.__table__.indexes} == {
        "ux_audiobooks_file_path", "ix_audiobooks_file_hash",
    }
    assert next(
        ix for ix in EBook.__table__.indexes if ix.name == "ux_ebooks_file_path"
    ).unique is True
    assert next(
        ix for ix in AudioBook.__table__.indexes if ix.name == "ux_audiobooks_file_path"
    ).unique is True


# ---------- the field list and the model stay in step, both directions ----------

def test_every_metadata_field_is_a_column_on_both_models():
    """A name in the list that is not a column would blow up at `setattr` time,
    on a code path (an ABS enrich, a scan) that no unit test necessarily hits."""
    ebook_names = set(_columns(EBook))
    audio_names = set(_columns(AudioBook))
    for field in MEDIA_METADATA_FIELDS:
        assert field in ebook_names, f"{field} is not an EBook column"
        assert field in audio_names, f"{field} is not an AudioBook column"


def test_every_shared_column_is_classified():
    """The other direction: a new column must be added to `MEDIA_METADATA_FIELDS`
    or to `NON_METADATA_COLUMNS` above. Forgetting is what silently drops the
    field from every copy site."""
    shared = set(_columns(EBook)) & set(_columns(AudioBook))

    assert set(MEDIA_METADATA_FIELDS).isdisjoint(NON_METADATA_COLUMNS)
    assert set(MEDIA_METADATA_FIELDS) | NON_METADATA_COLUMNS == shared


def test_the_field_groups_partition_the_full_list():
    """`MEDIA_METADATA_FIELDS` is assembled from four groups; the subsets used at
    the copy sites are built from those groups, so the partition is what stops a
    subset from quietly falling out of the whole."""
    groups = (
        MEDIA_CORE_FIELDS,
        MEDIA_DESCRIPTIVE_FIELDS,
        MEDIA_IDENTIFIER_FIELDS,
        MEDIA_FLAG_FIELDS,
    )
    concatenated = tuple(name for group in groups for name in group)

    assert concatenated == MEDIA_METADATA_FIELDS
    assert len(set(concatenated)) == len(concatenated)


def test_extracted_fields_are_everything_a_file_tag_can_supply():
    """The merge whitelist in `extract_metadata`: everything except the two
    flags, which no tagger writes."""
    assert MEDIA_EXTRACTED_FIELDS == (
        MEDIA_CORE_FIELDS + MEDIA_DESCRIPTIVE_FIELDS + MEDIA_IDENTIFIER_FIELDS
    )


def test_fill_if_null_includes_narrators_on_both_media_types():
    """The drift the issue found, pinned in the direction it was fixed.

    The ebook ingest copied eight fields and the audiobook ingest nine — the
    difference was `narrators`, missing on the ebook side even though
    `EBook.narrators` has existed all along and `extract_metadata` puts the value
    in `meta` for ebooks too. Both sides now use this one list, so an ebook whose
    file carries a narrator tag keeps it.
    """
    assert "narrators" in MEDIA_FILL_IF_NULL_FIELDS
    assert MEDIA_FILL_IF_NULL_FIELDS == MEDIA_DESCRIPTIVE_FIELDS + MEDIA_IDENTIFIER_FIELDS
    assert set(MEDIA_FILL_IF_NULL_FIELDS) <= set(MEDIA_METADATA_FIELDS)


def test_metadata_update_schema_matches_the_field_list():
    """The PATCH body is the user-facing half of the same list. It stays
    hand-typed (each field needs its own type), so this is the gate: a field
    added to `MEDIA_METADATA_FIELDS` and not to `MetadataUpdate` would be
    silently unsettable through the API."""
    assert set(MetadataUpdate.model_fields) == set(MEDIA_METADATA_FIELDS)


def test_fields_to_compare_is_derived_not_retyped():
    """The pair-discrepancy list is a different list on purpose — it drops the
    identifiers (an ISBN mismatch between an ebook and its audiobook is normal)
    and adds `cover_path`. It is still built from the shared groups."""
    assert tuple(FIELDS_TO_COMPARE) == (
        MEDIA_CORE_FIELDS + MEDIA_DESCRIPTIVE_FIELDS + MEDIA_FLAG_FIELDS + ("cover_path",)
    )


# ---------- the public JSON shapes are unchanged ----------

# Snapshotted from the tree before the shared base class landed (issue #257).
# These are the keys the web client and the Android DTOs read; the response
# models are now `MediaResponseBase` subclasses, and this is what proves the
# refactor did not add, drop or rename one.
_EXPECTED_EBOOK_RESPONSE_FIELDS = frozenset({
    "id", "title", "author", "filename", "file_path", "file_size", "format",
    "series", "series_index", "metadata_source", "metadata_pattern",
    "uploaded_at", "description", "publisher", "publish_year", "language",
    "genres", "tags", "isbn", "asin", "narrators", "is_explicit",
    "is_abridged", "cover_path", "acknowledged",
})
_EXPECTED_AUDIOBOOK_RESPONSE_FIELDS = _EXPECTED_EBOOK_RESPONSE_FIELDS | {"duration_seconds"}


def test_ebook_response_field_set_is_unchanged():
    assert set(EBookResponse.model_fields) == _EXPECTED_EBOOK_RESPONSE_FIELDS


def test_audiobook_response_field_set_is_unchanged():
    assert set(AudioBookResponse.model_fields) == _EXPECTED_AUDIOBOOK_RESPONSE_FIELDS


def test_response_models_stay_optional_where_they_were_optional():
    """Sharing a base is only safe if it does not tighten a field: a key that
    used to be allowed to be null must still be. Required-ness is snapshotted
    via the JSON schema's `required` list, which is what a generated client
    reads."""
    ebook_required = set(EBookResponse.model_json_schema()["required"])
    audio_required = set(AudioBookResponse.model_json_schema()["required"])

    assert ebook_required == {
        "id", "title", "author", "filename", "file_size", "format", "uploaded_at",
    }
    assert audio_required == ebook_required | {"duration_seconds"}
