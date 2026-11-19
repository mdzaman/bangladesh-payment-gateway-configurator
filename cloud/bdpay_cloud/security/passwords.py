"""Password hashing (Argon2id) and a length-first password policy."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # argon2-cffi defaults follow RFC 9106

MIN_LENGTH = 10
MAX_LENGTH = 200
_COMMON = {
    "password",
    "password1",
    "password12",
    "password123",
    "passw0rd",
    "1234567890",
    "12345678910",
    "qwertyuiop",
    "qwerty12345",
    "iloveyou12",
    "bangladesh",
    "bangladesh1",
    "bangladesh123",
    "0123456789",
    "1q2w3e4r5t",
    "letmein123",
    "administrator",
    "changeme123",
    "welcome123",
}


def use_fast_parameters_for_tests() -> None:
    """Cheap hashes so a test-suite that signs up hundreds of users stays fast."""
    global _hasher
    _hasher = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except Exception:
        return False


def burn(password: str) -> None:
    """Spend the same time as a real check when the account does not exist, so
    response time does not reveal which emails are registered."""
    verify_password(_dummy_hash(), password)


_DUMMY: dict[int, str] = {}


def _dummy_hash() -> str:
    key = id(_hasher)
    if key not in _DUMMY:
        _DUMMY.clear()
        _DUMMY[key] = _hasher.hash("not-a-real-password")
    return _DUMMY[key]


def password_problem(password: str, *, email: str = "", name: str = "") -> str | None:
    """None if acceptable, otherwise what to fix."""
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters"
    if len(password) > MAX_LENGTH:
        return f"Use at most {MAX_LENGTH} characters"
    lowered = password.lower()
    if lowered in _COMMON or len(set(password)) < 4:
        return "That password is too easy to guess"
    local = email.split("@")[0].lower()
    if (
        (email and lowered == email.lower())
        or (len(local) >= 4 and lowered == local)
        or (len(name) >= 4 and lowered == name.lower())
    ):
        return "Do not use your name or email as the password"
    return None
