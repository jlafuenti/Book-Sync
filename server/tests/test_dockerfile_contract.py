"""
Both images must ship the NLTK sentence tokenizer, not fetch it (issue #249).

`punkt_tab` is on the path of every pipeline run and every realign
(`epub_parser.extract_book_sentences`). An image that downloads it at runtime is
non-hermetic: a restricted-egress deployment fails at the EPUB-extraction step
*after* a multi-hour transcription has already finished, and on the server side a
slow GitHub CDN once stalled the FastAPI lifespan outright (issue #322).

The two Dockerfiles have drifted before — the Jetson one baked the corpus while
the server one did not — so the same contract is asserted against both from one
place, and it is the *whole* contract:

* the corpus is downloaded during the build, and
* into an explicit shared directory rather than the build user's home, which
  changes with the container user, and
* `NLTK_DATA` names that directory, so nothing depends on the path happening to
  be one of nltk's compiled-in defaults.

No CI job builds either image, so a line scan is the only guard available.
"""

import os

import pytest

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_SERVER_DIR)

# Both images provision here. It is already on `nltk.data.path` by default,
# which keeps the runtime working even if NLTK_DATA is unset by a wrapper.
NLTK_DATA_DIR = "/usr/local/share/nltk_data"

DOCKERFILES = {
    "server": os.path.join(_SERVER_DIR, "Dockerfile"),
    "jetson": os.path.join(_REPO_ROOT, "jetson", "Dockerfile"),
}


def _instructions(path: str) -> list[str]:
    """Non-comment, non-blank lines of a Dockerfile."""
    with open(path, encoding="utf-8") as fh:
        return [ln.strip() for ln in fh
                if ln.strip() and not ln.strip().startswith("#")]


def _assert_bakes_punkt_tab(path: str, image: str) -> None:
    lines = _instructions(path)

    provisioning = [ln for ln in lines
                    if "nltk" in ln.lower() and "punkt_tab" in ln]
    assert provisioning, (
        f"{image}: the image must download punkt_tab at build time. Without it "
        "every fresh container fetches the corpus over the network on first "
        "tokenize, which fails outright with no egress and stalls startup on a "
        "slow CDN (issues #249, #322)."
    )
    assert any(NLTK_DATA_DIR in ln for ln in provisioning), (
        f"{image}: provision into {NLTK_DATA_DIR}. A bare `nltk.download()` "
        "writes to the *build* user's home, which the runtime user may not "
        "share, so the baked copy is silently invisible and the container "
        "downloads it again anyway."
    )
    assert any(
        ln.startswith("ENV") and "NLTK_DATA" in ln and NLTK_DATA_DIR in ln
        for ln in lines
    ), (
        f"{image}: set ENV NLTK_DATA={NLTK_DATA_DIR} so the search path is "
        "declared by the image rather than inherited from nltk's compiled-in "
        "defaults."
    )


def test_server_image_bakes_the_nltk_tokenizer_data():
    _assert_bakes_punkt_tab(DOCKERFILES["server"], "server/Dockerfile")


def test_jetson_image_bakes_the_nltk_tokenizer_data():
    _assert_bakes_punkt_tab(DOCKERFILES["jetson"], "jetson/Dockerfile")


@pytest.mark.parametrize("image", sorted(DOCKERFILES))
def test_the_dockerfile_under_test_exists(image):
    """Guard against a rename quietly turning both tests into no-ops."""
    assert os.path.isfile(DOCKERFILES[image]), DOCKERFILES[image]
