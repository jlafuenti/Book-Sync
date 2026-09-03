"""The two version numbers this server advertises (issue #174).

They answer different questions and move independently.

``APP_VERSION`` is the human-facing release string — what ``GET /`` reports,
what OpenAPI shows, and what an operator quotes in a bug report. It changes
every release. Nothing branches on it.

``API_VERSION`` is a single integer that clients *do* branch on. It is the
compatibility contract between this server and the Android app, which is
distributed through Play and therefore updates on the user's schedule while the
server updates on the operator's — the two will drift, and before this existed
the drift surfaced as a 404 or a deserialisation error with nothing saying which
side was stale.

**Bump ``API_VERSION`` only for a change that a current client cannot survive**:
removing or renaming a field it reads, changing a field's type or units,
removing an endpoint, or making a previously optional request field required.
Adding an endpoint, adding a response field, or relaxing a requirement is not a
break — clients ignore what they do not know about, and bumping for those would
cry wolf until the warning is ignored.

When it does get bumped, `docs/android.md` and `docs/operations.md` describe
what has to happen on each side.
"""

APP_VERSION = "0.1.0"

API_VERSION = 1
