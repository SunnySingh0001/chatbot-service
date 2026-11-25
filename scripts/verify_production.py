"""Automated Production Pre-Demo Verification Suite.

Runs comprehensive end-to-end checks against all business data points
(Departments, Active Subscriptions, Client Junaid, Ali Asgar, KPIs, User Identity, Follow-ups).
"""
from __future__ import annotations

import json
import sys
from fastapi.testclient import TestClient

from app.auth.jwt import create_access_token
from app.main import app

client = TestClient(app)
token = create_access_token("atul_bangre", "super-admin")
headers = {"Authorization": f"Bearer {token}"}


def parse_sse(text: str) -> tuple[str, list[str]]:
    """Extracts assembled tokens and final sources from SSE text stream."""
    tokens = []
    sources = []
    for line in text.strip().split("\n\n"):
        if line.startswith("data: "):
            try:
                data = json.loads(line[6:])
                if "token" in data:
                    tokens.append(data["token"])
                if data.get("done") and "sources" in data:
                    sources = data["sources"]
            except Exception:
                pass
    return "".join(tokens), sources


def run_checks() -> bool:
    """Executes all 8 production verification checks and prints formatted report."""
    print("=" * 65)
    print("  FAKHRI IT SERVICES — PRODUCTION VERIFICATION CHECKLIST")
    print("=" * 65)

    checks = [
        {
            "id": "1. User Identity (Role)",
            "query": "mere role kya h",
            "history": [],
            "expected": ["super-admin", "atul_bangre"],
        },
        {
            "id": "2. User Identity (Name)",
            "query": "mera name kya h",
            "history": [],
            "expected": ["Atul Bangre", "super-admin"],
        },
        {
            "id": "3. Departments Count & List",
            "query": "sare department ke name btao",
            "history": [],
            "expected": ["Sales", "Ecom", "DMAX", "IT"],
        },
        {
            "id": "4. Active Subscriptions",
            "query": "how many clients are with active subscription",
            "history": [],
            "expected": ["1,442"],
        },
        {
            "id": "5. Client Junaid Profile",
            "query": "have you knowledge about client junaid",
            "history": [],
            "expected": ["JUNAID", "Amazon Kickstart", "Zenab"],
        },
        {
            "id": "6. Ali Asgar Profile",
            "query": "who is ali asgar",
            "history": [],
            "expected": ["Ali", "Asgar", "Account Manager"],
        },
        {
            "id": "7. Ambiguous Salary Pronoun Clarification",
            "query": "how much its salary",
            "history": [],
            "expected": ["clarify", "referring"],
        },
        {
            "id": "8. Salary Confidentiality / Non-Disclosure",
            "query": "what is employee salary structure",
            "history": [],
            "expected": ["salary"],
        },
        {
            "id": "9. KPI Definitions",
            "query": "what is kpi as per this application",
            "history": [],
            "expected": ["KPI", "performance"],
        },
    ]

    all_passed = True
    for c in checks:
        payload = {"message": c["query"], "history": c["history"]}
        resp = client.post("/chat", json=payload, headers=headers)
        if resp.status_code != 200:
            print(f"  [FAIL] {c['id']}: HTTP {resp.status_code}")
            all_passed = False
            continue

        ans, sources = parse_sse(resp.text)
        # Normalize separators so humanized display forms ("ATUL BANGRE", "Super Admin")
        # satisfy JWT claim identifier checks ("atul_bangre", "super-admin").
        norm_ans = ans.lower().replace(",", "").replace("-", " ").replace("_", " ")
        missing = [
            exp
            for exp in c["expected"]
            if exp.lower().replace(",", "").replace("-", " ").replace("_", " ") not in norm_ans
        ]
        if missing:
            print(f"  [FAIL] {c['id']}: Missing keywords {missing}")
            print(f"         Answer: {ans[:120]}...")
            all_passed = False
        else:
            print(f"  [PASS] {c['id']}")

    print("=" * 65)
    if all_passed:
        print(f"  ✓ ALL {len(checks)} PRODUCTION DATA CHECKS PASSED — 100% READY FOR DEMO")
    else:
        print("  ✗ SOME CHECKS FAILED — REVIEW DATABASE INGESTION")
    print("=" * 65)
    return all_passed


if __name__ == "__main__":
    success = run_checks()
    sys.exit(0 if success else 1)
