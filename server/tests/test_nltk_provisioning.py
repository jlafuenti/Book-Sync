"""
Importing a module must not touch the network (issue #322).

This took production down on 2026-08-31. `epub_parser` and `transcription` each
ran `nltk.download("punkt_tab")` at **module import time**, with no timeout. The
routers import them inside the FastAPI lifespan, so the download happened
synchronously on the event loop: a slow GitHub CDN response left the container
`unhealthy` and the site returning 502 for over five minutes at 0% CPU, with one
established connection to 185.199.109.133:443 and nothing else outstanding.

`docker compose restart server` cleared it, which is the tell — nothing was wrong
with the build or the database. It is a coin flip on how GitHub answers at
container start: the same image had come up in 21 seconds an hour earlier.

The real fix is provisioning the corpus in the image so the runtime never asks.
These tests cover the two halves that CI can see: nothing downloads at import,
and the tokenizers still ensure the data before they use it.
"""

import importlib
import os

import nltk
import pytest


def _sabotage(monkeypatch):
    """Make any download attempt loud, and pretend the corpus is missing.

    Forcing the LookupError is what makes this meaningful: without it a machine
    that already has punkt_tab cached would take the `find` branch and pass no
    matter what the import does.
    """
    def _boom(*args, **kwargs):
        raise AssertionError(
            "nltk.download() ran during import. That is the #322 outage: it "
            "blocks the FastAPI lifespan with no timeout, so a slow CDN means "
            "the server never starts at all."
        )

    def _missing(*args, **kwargs):
        raise LookupError("punkt_tab")

    monkeypatch.setattr(nltk, "download", _boom)
    monkeypatch.setattr(nltk.data, "find", _missing)


@pytest.mark.parametrize("module", ["services.epub_parser", "services.transcription"])
def test_importing_a_tokenizing_module_makes_no_network_call(monkeypatch, module):
    _sabotage(monkeypatch)
    mod = importlib.import_module(module)
    importlib.reload(mod)  # re-runs module-level code; raises before the fix


def test_ensure_punkt_is_called_before_tokenizing_epub_text(monkeypatch):
    """Lazy is only safe if something actually calls it."""
    from services import epub_parser, nltk_data

    calls = []
    monkeypatch.setattr(nltk_data, "ensure_punkt", lambda: calls.append(1))
    monkeypatch.setattr(epub_parser, "ensure_punkt", lambda: calls.append(1))
    monkeypatch.setattr(nltk, "sent_tokenize", lambda text: [text])

    epub_parser._split_into_sentences("A sentence long enough to survive filtering.")

    assert calls, (
        "_split_into_sentences tokenized without ensuring the corpus. Moving the "
        "download out of import scope only helps if the call sites ask for it."
    )


def test_the_image_provisions_the_corpus_at_build_time():
    """The only guard CI can offer: no workflow builds the Docker image.

    Baking punkt_tab into the image is what actually ends the outage; nothing
    else here would notice if that line were deleted.
    """
    dockerfile = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Dockerfile"
    )
    with open(dockerfile, encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh if not ln.strip().startswith("#")]

    provisioning = [ln for ln in lines if "nltk" in ln.lower() and "punkt_tab" in ln]
    assert provisioning, (
        "server/Dockerfile must download punkt_tab at build time. Without it every "
        "fresh container fetches the corpus from GitHub on first import, which is "
        "the #322 outage."
    )
    assert any("/usr/local/share/nltk_data" in ln for ln in provisioning), (
        "provision into /usr/local/share/nltk_data -- it is already on "
        "nltk.data.path, unlike a home-directory default that changes with the "
        "container user."
    )


# ---------------------------------------------------------------------------
# The fallback's failure path (issue #249)
# ---------------------------------------------------------------------------
#
# The fallback stays, for a bare dev checkout. What it must not do is fail
# quietly: `nltk.download(quiet=True)` returns False on a host with no egress,
# and the caller then dies at EPUB extraction -- after a multi-hour
# transcription -- with a LookupError that names nothing useful. The log line is
# the only place the real cause can appear.

def _force_download(monkeypatch, result):
    """Pretend the corpus is missing and the download returns `result`."""
    from services import nltk_data

    monkeypatch.setattr(nltk_data, "_ensured", False)
    monkeypatch.setattr(nltk.data, "find", lambda *a, **k: (_ for _ in ()).throw(
        LookupError("punkt_tab")))
    if isinstance(result, Exception):
        def _download(*a, **k):
            raise result
    else:
        def _download(*a, **k):
            return result
    monkeypatch.setattr(nltk, "download", _download)
    return nltk_data


def test_a_failed_download_logs_a_warning(monkeypatch, caplog):
    """`quiet=True` returning False must not be the only trace of the failure."""
    nltk_data = _force_download(monkeypatch, False)

    with caplog.at_level("WARNING"), pytest.raises(RuntimeError):
        nltk_data.ensure_punkt()

    failures = [
        r for r in caplog.records
        if r.levelname == "WARNING"
        and "punkt_tab" in r.getMessage()
        and any(w in r.getMessage().lower() for w in ("fail", "could not"))
    ]
    assert failures, (
        "a failed punkt_tab download logged no WARNING naming the failure. An "
        "offline container must say why extraction is about to die, not only "
        f"that it was about to try. Records: {[r.getMessage() for r in caplog.records]}"
    )


def test_a_download_that_raises_also_logs_a_warning(monkeypatch, caplog):
    """A refused connection is the same operational story as a False return."""
    nltk_data = _force_download(monkeypatch, OSError("network unreachable"))

    with caplog.at_level("WARNING"), pytest.raises(RuntimeError):
        nltk_data.ensure_punkt()

    assert any(
        r.levelname == "WARNING" and "network unreachable" in r.getMessage()
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]


def test_a_successful_download_logs_no_failure(monkeypatch, caplog):
    """The warning has to distinguish the two outcomes to be worth reading."""
    nltk_data = _force_download(monkeypatch, True)

    with caplog.at_level("WARNING"):
        nltk_data.ensure_punkt()

    assert not [
        r for r in caplog.records
        if any(w in r.getMessage().lower() for w in ("fail", "could not"))
    ]
