"""
Development utility to generate test JWT tokens for employee and hr roles.

Usage:
    python -m scripts.generate_tokens
"""
from __future__ import annotations

from datetime import timedelta

from app.auth.jwt import create_access_token


def generate_dev_tokens() -> dict[str, str]:
    """Generates 24-hour test tokens for employee and hr personas."""
    delta = timedelta(hours=24)
    employee_token = create_access_token(
        user_id="emp_alice_101",
        role="employee",
        expires_delta=delta,
    )
    hr_token = create_access_token(
        user_id="hr_bob_202",
        role="hr",
        expires_delta=delta,
    )
    return {
        "employee": employee_token,
        "hr": hr_token,
    }


def main() -> None:
    """Prints generated development JWT tokens and ready-to-use curl commands."""
    tokens = generate_dev_tokens()
    sep = "─" * 65

    print("\n" + sep)
    print("DEVELOPMENT JWT TOKENS (Valid for 24 Hours)")
    print(sep)

    print("\n[1] Role: employee (user_id: emp_alice_101)")
    print(f"Token:\n{tokens['employee']}")
    print("\nCurl Test Command:")
    print(
        f'curl -X POST http://localhost:8000/chat '
        f'-H "Authorization: Bearer {tokens["employee"]}" '
        f'-H "Content-Type: application/json" '
        f'-d \'{{"message": "What is the leave policy?"}}\''
    )

    print("\n" + sep)
    print("[2] Role: hr (user_id: hr_bob_202)")
    print(f"Token:\n{tokens['hr']}")
    print("\nCurl Test Command:")
    print(
        f'curl -X POST http://localhost:8000/chat '
        f'-H "Authorization: Bearer {tokens["hr"]}" '
        f'-H "Content-Type: application/json" '
        f'-d \'{{"message": "What are the salary bands?"}}\''
    )
    print(sep + "\n")


if __name__ == "__main__":
    main()
