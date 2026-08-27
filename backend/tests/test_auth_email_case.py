"""Email case must not decide whether you can log in.

``users.email`` has no ``COLLATE NOCASE`` and ``auth.login`` looks the user up
with ``User.email == body.email``, so before normalization a single capital
letter (autofill, iOS/macOS autocapitalize, a password manager that saved the
address capitalized) missed the row entirely. ``login`` then took the
``user is None`` branch and raised the generic "Invalid email or password" --
blaming the password for what was really an email-case miss, without ever
checking the password.

Register normalizes the same way, so ``Foo@x.com`` and ``foo@x.com`` can no
longer become two separate accounts past the unique index.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.auth import LoginRequest, RegisterRequest


@pytest.mark.parametrize(
    "submitted",
    [
        "hustleequeen@gmail.com",
        "Hustleequeen@gmail.com",
        "HustleeQueen@gmail.com",
        "HUSTLEEQUEEN@GMAIL.COM",
        "hustleequeen@Gmail.com",
    ],
)
def test_login_email_normalized_to_stored_form(submitted: str) -> None:
    assert LoginRequest(email=submitted, password="x").email == (
        "hustleequeen@gmail.com"
    )


def test_register_normalizes_identically_to_login() -> None:
    # If these ever diverge, you can register an address you cannot log into.
    mixed = "MixedCase@Example.COM"
    registered = RegisterRequest(
        email=mixed, password="a-long-enough-password", name="X"
    ).email
    assert registered == LoginRequest(email=mixed, password="x").email
    assert registered == "mixedcase@example.com"


def test_password_is_untouched_by_normalization() -> None:
    # Only the email is lowercased -- lowercasing the password would silently
    # widen every account's credential space.
    secret = "CorrectHorse-BatteryStaple"
    assert LoginRequest(email="A@B.com", password=secret).password == secret


def test_still_rejects_a_non_email() -> None:
    with pytest.raises(ValidationError):
        LoginRequest(email="not-an-email", password="x")
