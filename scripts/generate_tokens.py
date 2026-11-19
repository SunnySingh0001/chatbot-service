"""Development utility generating test JWT tokens for verified real team members and roles."""
from __future__ import annotations

from datetime import timedelta

from app.auth.jwt import create_access_token


def generate_dev_tokens() -> dict[str, str]:
    """Generates 24-hour test tokens for verified company team members."""
    delta = timedelta(hours=24)
    return {
        "super_admin": create_access_token("atul_bangre", "super-admin", expires_delta=delta),
        "founder": create_access_token("abdul_quadir", "founder", expires_delta=delta),
        "hr": create_access_token("disha_damwani", "hr", expires_delta=delta),
        "operations": create_access_token("zainab_husain", "operations", expires_delta=delta),
        "employee_vivek": create_access_token("vivek_parihar", "employee", expires_delta=delta),
        "employee_aashi": create_access_token("aashi_jain", "employee", expires_delta=delta),
        "client": create_access_token("client_portal_user", "client", expires_delta=delta),
    }


def _print_persona(role: str, user_id: str, title: str, token: str, sample_q: str, sep: str) -> None:
    """Prints token and sample curl command for a single verified persona."""
    print(f"\n{title} | user_id: '{user_id}' | role: '{role}'")
    print(f"Token:\n{token}")
    print("\nCurl Test Command:")
    print(
        f'curl -X POST http://localhost:8000/chat '
        f'-H "Authorization: Bearer {token}" '
        f'-H "Content-Type: application/json" '
        f'-d \'{{"message": "{sample_q}"}}\''
    )
    print(sep)


def main() -> None:
    """Prints generated development JWT tokens and ready-to-use curl commands for team members."""
    tokens = generate_dev_tokens()
    sep = "─" * 68

    print("\n" + sep)
    print("FAKHRI IT SERVICES — REAL TEAM MEMBER JWT TOKENS (Valid 24 Hours)")
    print(sep)

    personas = [
        ("super-admin", "atul_bangre", "⚡ SUPER ADMIN (Platform Full Access)", tokens["super_admin"], "What are all company roles and scopes?"),
        ("founder", "abdul_quadir", "👑 FOUNDER (Root Access)", tokens["founder"], "What are our departments and roles?"),
        ("hr", "disha_damwani", "👥 HR MANAGEMENT", tokens["hr"], "What are the active job openings in our company?"),
        ("operations", "zainab_husain", "⚙️ HEAD OF OPERATIONS", tokens["operations"], "What Amazon FBA operations services do we provide?"),
        ("employee", "vivek_parihar", "🎬 VIDEO EDITOR (Employee)", tokens["employee_vivek"], "What are our services and job openings?"),
        ("employee", "aashi_jain", "📋 ACCOUNT MANAGER (Employee)", tokens["employee_aashi"], "How do clients contact their account manager?"),
        ("client", "client_portal_user", "🌐 CLIENT USER (Public Access Only)", tokens["client"], "What are the company contact details and services?"),
    ]

    for role, user_id, title, tok, q in personas:
        _print_persona(role, user_id, title, tok, q, sep)


if __name__ == "__main__":
    main()
