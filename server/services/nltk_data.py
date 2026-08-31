"""
Making sure the NLTK sentence tokenizer has its data, without hanging (#322).

`epub_parser` and `transcription` each used to do this at **module import time**:

    try:
        nltk.data.find("tokenizers/punkt_tab")
    except LookupError:
        nltk.download("punkt_tab", quiet=True)

That download has no timeout, and both modules are pulled in during the FastAPI
lifespan. On 2026-08-31 a slow GitHub CDN response left the container `unhealthy`
and the site returning 502 for over five minutes at 0% CPU -- one established
connection to a GitHub address and nothing else outstanding. `docker compose
restart server` cleared it, because the retry happened to be fast. No network to
GitHub meant no server at all, not merely no transcription.

The real fix is `server/Dockerfile`, which now downloads the corpus at build time
into `/usr/local/share/nltk_data`, so a deployed container always takes the
`find` branch and never reaches the network. What remains here is a bounded
fallback for a bare dev machine or a `pip install -r requirements.txt` checkout,
called lazily by the two functions that actually tokenize.

Importing this module does nothing.
"""

import logging
import socket

import nltk

logger = logging.getLogger(__name__)

_PUNKT = "tokenizers/punkt_tab"

# Long enough for a slow-but-working connection, short enough that a stalled one
# fails while someone is still watching. The container never uses this path.
_DOWNLOAD_TIMEOUT_SECONDS = 30

# The check is a filesystem search; the tokenizers call it per document.
_ensured = False


def ensure_punkt() -> None:
    """Guarantee punkt_tab is available, or raise with a usable message.

    Raises:
        RuntimeError: the corpus is absent and could not be fetched. Failing
            loudly here is the point -- the old code's silent, unbounded wait is
            what turned a missing corpus into an unexplained dead server.
    """
    global _ensured
    if _ensured:
        return

    try:
        nltk.data.find(_PUNKT)
        _ensured = True
        return
    except LookupError:
        pass

    logger.warning(
        "NLTK punkt_tab not found locally; downloading. A deployed image should "
        "never reach this -- check the nltk.downloader step in server/Dockerfile."
    )

    # nltk.download goes through urllib, which honours the default socket
    # timeout. Setting it process-wide is blunt, and it is restored immediately;
    # the alternative is the unbounded wait that caused the outage.
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(_DOWNLOAD_TIMEOUT_SECONDS)
    try:
        ok = nltk.download("punkt_tab", quiet=True)
    except Exception as exc:
        raise RuntimeError(
            "Could not download the NLTK punkt_tab corpus "
            f"({type(exc).__name__}: {exc}). Provision it in the image "
            "(see server/Dockerfile) or run: "
            "python -m nltk.downloader punkt_tab"
        ) from exc
    finally:
        socket.setdefaulttimeout(previous)

    if not ok:
        raise RuntimeError(
            "NLTK reported failure downloading punkt_tab. Provision it in the "
            "image (see server/Dockerfile) or run: "
            "python -m nltk.downloader punkt_tab"
        )

    _ensured = True
