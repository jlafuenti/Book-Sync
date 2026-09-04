#!/usr/bin/env python3
"""
Break-glass password reset for a locked-out account (issue #204).

Ordinary users have a recovery path already: an admin resets them from
System -> User Management, which is `POST /api/users/{id}/reset-password`. The
account with no path at all is the **sole superadmin**, because
`database.bootstrap_superadmin` only mints an account on an *empty* database and
promotes nobody once real users exist. Recovering that used to mean an ad-hoc
`python -c` with `hash_password` and a hand-written UPDATE inside the container.

This is that recovery, written down and tested. It is deliberately a script and
not an endpoint: it runs inside the server container against the configured
`DATABASE_URL`, so holding shell on the host *is* the authorisation. An endpoint
would need an authorisation story of its own, and every such story is a new way
in.

    docker compose exec server python -m scripts.reset_password admin

It prints one generated password (or accepts `--password` if you would rather
choose), forces a change at next login, bumps `token_version` and revokes every
live session, re-activates a deactivated account, and writes an audit row. The
password itself never reaches the audit log or the server log.

**Self-service email reset is deliberately out of scope.** If it is ever added
it needs a signed, single-use, short-TTL token and its own rate limit; a
half-done reset endpoint is worse than none.

Exit code is 0 on success, 1 when the username is unknown or the supplied
password fails the policy.
"""

import argparse
import asyncio
import os
import secrets
import sys
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from database import async_session  # noqa: E402
from models.user import User  # noqa: E402
from routers.auth import hash_password, log_audit, revoke_all_sessions  # noqa: E402
from schemas import (  # noqa: E402
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    PASSWORD_POLICY_MESSAGE,
)

#: Bytes of entropy behind a generated password. `token_urlsafe(16)` yields 22
#: characters, comfortably inside the 8..128 policy and far stronger than
#: anything a human picks for a password they intend to replace in a minute.
GENERATED_PASSWORD_BYTES = 16


class UnknownUser(Exception):
    """No account with that username exists."""


async def reset_password(username: str, new_password: Optional[str] = None) -> str:
    """Reset one user's password and return the password now in force.

    Raises `UnknownUser` if the username is unknown and `ValueError` if an
    explicitly supplied password fails the shared policy — in both cases without
    touching a single row, so a typo cannot half-apply a reset.
    """
    if new_password is not None and not (
        PASSWORD_MIN_LENGTH <= len(new_password) <= PASSWORD_MAX_LENGTH
    ):
        raise ValueError(PASSWORD_POLICY_MESSAGE)

    password = new_password or secrets.token_urlsafe(GENERATED_PASSWORD_BYTES)

    async with async_session() as db:
        user = (await db.execute(
            select(User).where(User.username == username)
        )).scalar_one_or_none()
        if user is None:
            raise UnknownUser(username)

        user.hashed_password = hash_password(password)
        # The operator holding the shell is not necessarily the account's owner,
        # so the password printed here is a handover credential, not a chosen
        # one — the owner must replace it before they can use anything else.
        user.must_reset_password = True
        # Same pairing as the admin reset in `routers/users.py`: the bump kills
        # the tokens already in flight, the revoke stops the sessions behind
        # them being reusable.
        user.token_version += 1
        await revoke_all_sessions(db, user.id)
        # `is_active=False` is the database-level lockout. Leaving it set would
        # hand the operator a password that still cannot log in.
        user.is_active = True
        await db.flush()

        await log_audit(
            db, "password_reset",
            user_id=None,  # no acting admin: this ran from the container shell
            target_user_id=user.id,
            details=f"break-glass CLI reset for '{user.username}'",
        )
        await db.commit()

    return password


async def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m scripts.reset_password",
        description="Reset one user's password from inside the server container.",
    )
    ap.add_argument("username", help="The account to reset, e.g. admin")
    ap.add_argument(
        "--password",
        help=("Use this password instead of a generated one. Prefer the "
              "generated one: it does not end up in your shell history."),
    )
    args = ap.parse_args(argv)

    try:
        password = await reset_password(args.username, args.password)
    except UnknownUser:
        print(f"No user named '{args.username}'. "
              f"Check the spelling — usernames are case-sensitive.",
              file=sys.stderr)
        return 1
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    print(f"Password reset for '{args.username}'.")
    print(f"  New password: {password}")
    print("  This account must choose its own password at next sign-in, and "
          "every device it was signed in on has been signed out.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
