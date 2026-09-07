"""The library router still exports every name that moved out of it (issue #255).

`routers/library.py` was split into services by seam. Other routers
(`match`, `troubleshoot`, `import_sources`), the import scheduler and a large
share of the test suite import the moved names *from the router*, and several
tests stub them on the router module so the handlers pick the stub up. Each
seam therefore re-exports what it moved. This file pins that the re-export is
the same object as the service's — not a copy, not a wrapper — so a stub on
either module stays coherent with the other.
"""

import pytest

from routers import library

SEAMS = {
    "services.tag_writer": {
        "_write_ebook_metadata": "write_ebook_metadata",
        "_write_audiobook_metadata": "write_audiobook_metadata",
    },
    "services.metadata_extract": {
        name: name for name in (
            "REGEX_AUTHOR_SERIES_TITLE", "REGEX_SERIES_TITLE",
            "is_path_pattern", "get_filename_patterns", "compute_file_hash",
            "extract_title_from_filename", "parse_filename_metadata_with_settings",
            "_read_embedded_metadata", "extract_metadata", "sanitize_filename",
            "_extract_and_save_cover",
        )
    },
    "services.auto_match": {
        name: name for name in (
            "AUTO_MATCH_TITLE_THRESHOLD", "AUTO_MATCH_TITLE_THRESHOLD_NO_AUTHOR",
            "AUTO_MATCH_AUTHOR_GATE", "AUTO_MATCH_AUTHOR_BOOST",
            "AUTO_MATCH_AUTHOR_BOOST_POINTS", "AUTO_MATCH_SERIES_THRESHOLD",
            "AUTO_MATCH_SERIES_INDEX_TOLERANCE",
            "_normalize_for_comparison", "_normalize_author", "_parse_series_index",
            "_series_compatible", "_auto_pair_excluded", "_score_candidate",
            "auto_match_books",
        )
    },
    "services.library_scan": {
        name: name for name in (
            "EBOOK_EXTENSIONS", "AUDIOBOOK_EXTENSIONS", "SCAN_COMMIT_BATCH", "_Batch",
            "_hash_and_size", "_find_by_path", "_insert_or_reread",
            "_ingest_one_ebook", "_ingest_one_audiobook", "_maybe_load_abs_index",
            "_multi_file_groups", "_walk_tree", "_classify_tree",
            "scan_files_impl", "scan_library_impl",
        )
    },
    "services.abs_metadata": {"_load_abs_settings": "load_abs_settings"},
    "services.library_browse": {
        name: name for name in (
            "PAGE_DEFAULT_LIMIT", "PAGE_MAX_LIMIT", "SEARCH_MAX_RESULTS", "_LIKE_ESCAPE",
            "_like_term", "_search_clause", "_library_order", "_paginate", "_list_media",
            "_pairs_base", "_PAIR_LOADS", "_int_null", "_paired_ebook", "_paired_audiobook",
            "_pair_arm", "_media_arm", "_browse_arms", "_browse_subquery", "_browse_order",
            "_hydrate_items", "_count", "search_impl", "list_items_impl", "facets_impl",
        )
    },
    "services.discrepancies": {
        name: name for name in (
            "FIELDS_TO_COMPARE", "_pair_has_discrepancies",
            "find_discrepancies_impl", "apply_resolution", "ignore_fields_impl",
        )
    },
}

CASES = [
    (module, router_name, service_name)
    for module, names in SEAMS.items()
    for router_name, service_name in names.items()
]


@pytest.mark.parametrize("module, router_name, service_name", CASES,
                         ids=[f"{m}.{s}" for m, _, s in CASES])
def test_the_router_re_exports_the_service_object_itself(module, router_name, service_name):
    service = __import__(module, fromlist=[service_name])
    assert getattr(library, router_name) is getattr(service, service_name)
