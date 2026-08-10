from __future__ import annotations

from backend.app.core.auth import hash_password, verify_password


def test_hash_password_uses_bcrypt_round_trip():
    hashed_password = hash_password("very-secure-password")

    assert hashed_password.startswith("$2")
    assert verify_password("very-secure-password", hashed_password)
    assert not verify_password("wrong-password", hashed_password)


def test_hash_password_handles_overlong_passwords_consistently():
    password = "a" * 80
    equivalent_password = "a" * 72

    hashed_password = hash_password(password)

    assert verify_password(password, hashed_password)
    assert verify_password(equivalent_password, hashed_password)
