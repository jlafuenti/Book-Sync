"""
Password hashing characterization tests (issue #515).

passlib 1.7.4's bcrypt backend self-test raises under bcrypt 5 (it reads
``bcrypt.__about__.__version__``, removed upstream), so ``routers/auth.py``
now calls the ``bcrypt`` module directly instead of going through passlib's
``CryptContext``. These tests pin the two behaviors that make that swap safe:
a hash produced by the old passlib-based code still verifies, and the
72-byte truncation passlib did silently is preserved.
"""

import pytest

from routers.auth import hash_password, verify_password

# Produced with passlib 1.7.4 (CryptContext(schemes=["bcrypt"], deprecated="auto",
# bcrypt__rounds=4)) on the last commit before this change, hashing the literal
# password "correct horse" (not a real credential). Kept as a fixed constant so
# this test still exercises the old on-disk hash format after passlib is
# removed from requirements.txt.
_PASSLIB_HASH = "$2b$04$Os5./cxXoJRK3ScWmlJ91eyWmYJaVN.Zar99d5/QM0UiYgD8FaZtO"


def test_old_passlib_hash_still_verifies():
    assert verify_password("correct horse", _PASSLIB_HASH) is True


def test_new_hashes_use_2b_prefix_and_verify():
    hashed = hash_password("correct horse battery staple")
    assert hashed.startswith("$2b$")
    assert verify_password("correct horse battery staple", hashed) is True


def test_long_password_hashes_and_verifies_truncated():
    # bcrypt only looks at the first 72 bytes of input; passlib silently
    # truncated to match, and bcrypt >=4.1 raises ValueError instead of
    # truncating for us. hash_password/verify_password truncate by hand so a
    # password set under passlib (and thus effectively 72 bytes long) still
    # logs in, and so a >72-byte password never raises.
    long_password = "x" * 100
    truncated = "x" * 72

    hashed = hash_password(long_password)

    assert verify_password(long_password, hashed) is True
    assert verify_password(truncated, hashed) is True


def test_verify_password_false_on_malformed_hash():
    assert verify_password("x", "not-a-hash") is False


def test_verify_password_false_on_wrong_password():
    hashed = hash_password("correct horse")
    assert verify_password("wrong horse", hashed) is False
