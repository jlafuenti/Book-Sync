"""
Credentials must not survive into the access log (issue #284).

uvicorn's access logger writes the full request line, query string included. Two
kinds of secret reach it:

- **Media tokens** on cover and audio URLs. `<img>` and `<audio>` cannot send an
  Authorization header, so the token in the URL is deliberate (issue #50). They
  are short-lived, resource-scoped and `token_version`-bound, so the exposure is
  small -- but the log is durable, and the token is replayable for its lifetime.
- **Third-party API keys** on the admin "Test connection" calls. Those are
  long-lived and not resource-scoped. #284 moves them into a POST body, so this
  filter is the second line rather than the first -- but a
  `test-remote?url=...&key=...` line was found in the production log, so the
  first line had already failed once.

The filter mutates the record and returns True, unlike `EndpointFilter` and
`ImportPollingFilter` next to it, which drop records by returning False. That
difference is the whole reason this file exists: a filter that returned False
here would silently delete access logging for every media request.
"""

import logging

from main import AccessLogSecretFilter

_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJlLWhlcmU"


def _record(path: str) -> logging.LogRecord:
    """A record shaped the way uvicorn.access builds them.

    uvicorn passes the pieces as args and formats later, so the query string
    lives in args[2] -- not in the message. A filter that only inspected
    `getMessage()` would match nothing and look like it worked.
    """
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:52000", "GET", path, "1.1", 200),
        exc_info=None,
    )


def test_media_token_is_redacted():
    rec = _record(f"/api/files/covers/a.jpg?token={_JWT}")
    assert AccessLogSecretFilter().filter(rec) is True
    assert _JWT not in rec.getMessage()
    assert "token=[redacted]" in rec.getMessage()


def test_third_party_key_is_redacted():
    rec = _record("/api/settings/test-remote?url=http%3A%2F%2Fhost%3A9000&key=super-secret")
    assert AccessLogSecretFilter().filter(rec) is True
    assert "super-secret" not in rec.getMessage()


def test_the_path_and_status_survive():
    """Redaction must not cost us the log line -- we still need the request."""
    rec = _record(f"/api/files/covers/a.jpg?token={_JWT}")
    AccessLogSecretFilter().filter(rec)
    msg = rec.getMessage()
    assert "/api/files/covers/a.jpg" in msg
    assert "GET" in msg and "200" in msg


def test_an_ordinary_line_is_untouched():
    rec = _record("/api/library/pairs?page=1&limit=500")
    before = rec.getMessage()
    assert AccessLogSecretFilter().filter(rec) is True
    assert rec.getMessage() == before


def test_a_record_without_args_is_passed_through():
    """Not every record on this logger is an access line; none may be dropped."""
    rec = logging.LogRecord(
        name="uvicorn.access", level=logging.INFO, pathname=__file__, lineno=1,
        msg="something else entirely", args=None, exc_info=None,
    )
    assert AccessLogSecretFilter().filter(rec) is True
    assert rec.getMessage() == "something else entirely"
