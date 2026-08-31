"""
Logging filters that need to be importable without starting the app.

`main.py` creates its log directory at import time, so importing it from a test
tries to `mkdir /data/logs` — which works on a dev box and fails in CI with a
`PermissionError`. Anything that wants a unit test lives here instead; `main.py`
imports this and does the `addFilter` wiring.

The two noise filters (`EndpointFilter`, `ImportPollingFilter`) stay in `main.py`
where they were: they drop records and have no tests, and moving them would be
churn for its own sake.
"""

import logging
import re

# Query parameters whose values are credentials. `token` covers the media tokens
# that cover and audio URLs carry by design (issue #50); `key` covers the Jetson
# transcription key, and the ABS/Hardcover tokens before #284 moved them into a
# POST body.
_SECRET_PARAM = re.compile(r"((?:token|key|api_key|access_token)=)[^&\s]+", re.I)


class AccessLogSecretFilter(logging.Filter):
    """Strip credentials out of the logged request line (issue #284).

    uvicorn's access logger writes the full request line, query string included,
    and that log is durable. Media tokens are short-lived and resource-scoped, so
    the exposure there is small but real; the third-party API keys that used to
    ride on the "Test connection" calls were neither, and one was found in the
    production container's log.

    This filter **mutates the record and returns True**, unlike the two noise
    filters in `main.py` that drop records by returning False. That difference
    matters: returning False here would delete access logging for every media
    request instead of redacting it.

    uvicorn builds the record with the pieces in `args` and formats later, so the
    query string lives in `args[2]`, not in the message. A filter that inspected
    only `getMessage()` would match nothing and look like it worked.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) > 2 and isinstance(args[2], str):
            path = args[2]
            if "=" in path:
                redacted = _SECRET_PARAM.sub(r"\1[redacted]", path)
                if redacted != path:
                    record.args = args[:2] + (redacted,) + args[3:]
        return True
