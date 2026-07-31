"""
The restore ladder's decision layer, driven by the cross-platform golden
vectors in fixtures/sync_parity/restore_cases.json.

The ladder decides, in order, which anchors are worth trying when opening a
book. The text search itself needs the EPUB and so stays in the clients; the
*ordering and eligibility* is what has to agree across server, web and Android,
and it is where the bugs were.

The load-bearing invariant: **a position with any anchor never yields an empty
plan.** Treating "no precise hint" as "no position" is what opened a book at
page one and let the next autosave overwrite a real position with chapter 0.
"""

import json
import os

import pytest

from services.position_resolver import plan_restore

_FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures", "sync_parity", "restore_cases.json",
)

with open(_FIXTURE, encoding="utf-8") as fh:
    CASES = json.load(fh)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_restore_plan_matches_golden_vector(case):
    steps = plan_restore(case["position"], **case["context"])
    assert [s.kind for s in steps] == case["expected"], case["why"]


def test_any_anchor_always_yields_a_plan():
    """Stated as its own test because it is the invariant the whole design
    rests on, not just a property of the fixture list."""
    anchored = [
        {"anchor_revision": 1, "epub_chapter": 3},
        {"anchor_revision": 1, "epub_text_preview": "althea counted the ships at anchor"},
        {"anchor_revision": 1, "epub_progress_percent": 12.5},
        {"anchor_revision": 1, "audio_position_ms": 1000},
    ]
    for position in anchored:
        steps = plan_restore(position, spine_count=40, device_id="d", hint_kind="readium_locator")
        assert steps, f"{position} produced no restore plan"


def test_steps_carry_what_the_client_needs_to_execute_them():
    position = {
        "anchor_revision": 7,
        "epub_chapter": 12,
        "epub_sentence_index": 4,
        "epub_text_preview": "althea counted the ships at anchor",
        "epub_progress_percent": 46.81,
        "hints": [{"kind": "readium_locator", "device_id": "pixel",
                   "value": "{\"href\":\"ch12.xhtml\"}", "anchor_revision": 7}],
    }
    steps = {s.kind: s for s in plan_restore(
        position, spine_count=40, device_id="pixel", hint_kind="readium_locator")}

    assert steps["hint"].value == "{\"href\":\"ch12.xhtml\"}"
    assert steps["text"].text == "althea counted the ships at anchor"
    assert steps["text"].seed_chapter == 12
    assert steps["chapter"].chapter == 12
    assert steps["percent"].percent == 46.81
