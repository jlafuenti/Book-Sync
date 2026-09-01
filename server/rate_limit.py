"""Rate limiting for the auth endpoints.

Two independent layers guard ``POST /api/auth/login``:

1. **Per client IP** — slowapi's ``@limiter.limit("5/minute")``. Cheap and
   generic, but only as good as the client address: behind a reverse proxy or
   docker NAT every caller can collapse into a single bucket unless uvicorn is
   told which peers to trust (``FORWARDED_ALLOW_IPS``, issues #156 / #294).
2. **Per username** — :class:`FailedLoginTracker` below, checked inside the
   route. slowapi key functions are synchronous and cannot read the request
   body, so a username-keyed bucket can't be expressed as a decorator (issue
   #296). This is the layer that actually bounds a password-guessing run
   against a *known* account, regardless of how many source addresses it uses.

Both layers stay on: the IP bucket also covers username *spraying* (many
accounts, few guesses each), which the username bucket by design does not.

**Lockout-DoS trade-off.** Keying on the username means anyone who knows a
username can park it at the threshold and keep the real user out until the
window elapses. That is inherent to username keying, and it is mitigated, not
eliminated:

* the window is short (15 minutes by default), so the worst case is a delay,
  not an account takeover — and an attacker must keep spending requests, which
  the per-IP bucket meters;
* clearing on success is impossible *while* locked (the lock is checked before
  the password is verified — deliberately, so it can't be used as a
  password oracle), so the threshold is set high (10) — far above any
  plausible run of typos by a real user, who will also usually succeed and
  reset the counter long before reaching it.

Raising ``LOGIN_FAILURE_LIMIT`` weakens brute-force protection; lowering it
makes the lockout easier to weaponize. See docs/operations.md, "Login
throttling".

**Storage.** In-process dictionary — correct for the single-process uvicorn
deployment this ships as. Everything the route needs is behind the
:class:`FailedLoginTracker` interface, so swapping in a DB- or redis-backed
implementation (needed the day the server runs multiple workers, where each
worker would otherwise keep its own counts and multiply the effective
threshold) means replacing this one class.
"""

import hashlib
import time
from threading import Lock
from typing import Callable, Dict, List, Optional

from slowapi import Limiter
from slowapi.util import get_remote_address

from config import settings

limiter = Limiter(key_func=get_remote_address)


def normalize_username(username: str) -> str:
    """Fold a submitted username to its bucket key.

    Case-insensitive and whitespace-stripped so ``Alice``, ``alice`` and
    ``" alice "`` share one counter — otherwise trivial spelling variations
    would each get a fresh allowance.
    """
    return (username or "").strip().casefold()


class FailedLoginTracker:
    """Sliding-window counter of *failed* login attempts, keyed by username.

    Only failures are recorded; a successful password check clears the bucket.
    A username is locked while it holds at least ``limit`` failures inside the
    trailing ``window`` seconds.

    ``clock`` defaults to :func:`time.monotonic` (immune to wall-clock jumps)
    and is injectable for tests. ``max_tracked`` bounds memory: an attacker
    spraying distinct usernames would otherwise grow the dict without limit.
    """

    def __init__(
        self,
        clock: Optional[Callable[[], float]] = None,
        max_tracked: int = 10_000,
        normalize: Optional[Callable[[str], str]] = None,
        limit_supplier: Optional[Callable[[], int]] = None,
        window_supplier: Optional[Callable[[], int]] = None,
    ):
        self.clock: Callable[[], float] = clock or time.monotonic
        self.max_tracked = max_tracked
        # Injected so the same counter can guard a second endpoint with its own
        # key semantics and thresholds (issue #264). The defaults are the login
        # behaviour this class was written for, unchanged.
        self._normalize: Callable[[str], str] = normalize or normalize_username
        self._limit_supplier = limit_supplier or (lambda: settings.login_failure_limit)
        self._window_supplier = window_supplier or (
            lambda: settings.login_failure_window_seconds
        )
        self._failures: Dict[str, List[float]] = {}
        self._lock = Lock()

    # -- configuration (read live, so tests/ops can retune without a restart) --

    @property
    def limit(self) -> int:
        """Failures inside the window that trip the lock."""
        return max(1, int(self._limit_supplier()))

    @property
    def window(self) -> int:
        """Length of the sliding window, in seconds."""
        return max(1, int(self._window_supplier()))

    # -- queries ---------------------------------------------------------------

    def retry_after(self, username: str) -> Optional[int]:
        """Seconds until this username may try again, or ``None`` if not locked.

        The value is when the bucket drops back *below* the threshold: the
        moment the oldest failure that keeps it at ``limit`` ages out of the
        window.
        """
        key = self._normalize(username)
        limit = self.limit
        window = self.window
        with self._lock:
            recent = self._prune(key, window)
            if len(recent) < limit:
                return None
            # recent is ascending; dropping everything up to and including
            # index len-limit leaves limit-1 entries, i.e. unlocked.
            expires_at = recent[len(recent) - limit] + window
            remaining = expires_at - self.clock()
        # Never advertise 0 — a client that honors Retry-After would retry
        # immediately and be refused again on the boundary.
        return max(1, int(remaining) + 1)

    def failure_count(self, username: str) -> int:
        """Failures currently inside the window for this username."""
        key = self._normalize(username)
        window = self.window
        with self._lock:
            return len(self._prune(key, window))

    @property
    def tracked_usernames(self) -> int:
        """How many usernames currently hold state (for tests/introspection)."""
        with self._lock:
            return len(self._failures)

    # -- mutations -------------------------------------------------------------

    def record_failure(self, username: str) -> None:
        """Count one failed attempt against this username."""
        key = self._normalize(username)
        window = self.window
        with self._lock:
            recent = self._prune(key, window)
            recent.append(self.clock())
            self._failures[key] = recent
            self._evict_if_oversized(window)

    def clear(self, username: str) -> None:
        """Forget every failure for this username (called on a correct password)."""
        with self._lock:
            self._failures.pop(self._normalize(username), None)

    def reset(self) -> None:
        """Drop all state. Test hook; never called at runtime."""
        with self._lock:
            self._failures.clear()

    # -- internals (callers must hold self._lock) ------------------------------

    def _prune(self, key: str, window: int) -> List[float]:
        """Return this key's timestamps with anything outside the window dropped."""
        cutoff = self.clock() - window
        recent = [t for t in self._failures.get(key, ()) if t > cutoff]
        if not recent:
            self._failures.pop(key, None)
        else:
            self._failures[key] = recent
        return recent

    def _evict_if_oversized(self, window: int) -> None:
        """Bound the dict so a spray of distinct usernames can't exhaust memory."""
        if len(self._failures) <= self.max_tracked:
            return
        cutoff = self.clock() - window
        for key in [k for k, ts in self._failures.items() if not ts or ts[-1] <= cutoff]:
            del self._failures[key]
        if len(self._failures) <= self.max_tracked:
            return
        # Still oversized: drop the least recently active buckets. They are the
        # closest to expiring anyway, so this loses the least protection.
        stale = sorted(self._failures, key=lambda k: self._failures[k][-1])
        for key in stale[: len(self._failures) - self.max_tracked]:
            del self._failures[key]


#: Process-wide tracker used by the login route.
failed_logins = FailedLoginTracker()

#: Failed current-password checks on ``POST /api/auth/change-password`` (#264).
#:
#: Keyed by the authenticated user's id. **Not** by IP: behind Caddy and docker
#: NAT every client shares the proxy's address (issue #294), so an IP bucket here
#: would throttle the whole deployment together and protect nobody.
#:
#: The id is already canonical, so the key needs no folding — unlike a username,
#: which has to absorb case and whitespace.
failed_password_changes = FailedLoginTracker(
    normalize=lambda user_id: str(user_id),
    limit_supplier=lambda: settings.password_change_failure_limit,
    window_supplier=lambda: settings.password_change_failure_window_seconds,
)


#: Rejected ``POST /api/auth/refresh`` attempts, keyed by token subject (#264).
#:
#: The issue asked for a slowapi decorator keyed by the token's ``sub``. That is
#: not expressible: the token arrives in the request *body*, and slowapi key
#: functions are synchronous and cannot read it -- the same wall as issue #296,
#: described at the top of this module. The only decorator left keys on the
#: client address, which behind Caddy and docker NAT (#294) is one bucket for the
#: entire deployment.
#:
#: So this is checked in-handler like the login tracker, and counts only
#: failures. A valid refresh is cheap and, since #268, single-flighted by the web
#: client; a rejected one costs a DB lookup, and that is what is worth bounding.
#:
#: Keyed by the **token**, not its subject. Live testing showed why: with a
#: subject key, twenty rejected replays of one dead token also blocked that
#: user's freshly issued, perfectly valid one for the rest of the window --
#: anyone holding a single stale token could keep a user from renewing. Keying
#: on the token confines the lockout to the token being abused, and an attacker
#: cannot mint a new one to rotate around it without the signing key.
failed_refreshes = FailedLoginTracker(
    normalize=lambda token: hashlib.sha256(str(token).encode()).hexdigest(),
    limit_supplier=lambda: settings.refresh_failure_limit,
    window_supplier=lambda: settings.refresh_failure_window_seconds,
)
