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
