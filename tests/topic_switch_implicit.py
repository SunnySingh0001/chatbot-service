"""Implicit topic-switch multi-turn evaluation cases without explicit transition markers.

Each case pairs a 2-to-4 turn conversation on one topic (leave, notice, expenses) with a follow-up
question on a different topic from company policies, phrased colloquially as employees type.
"""
from __future__ import annotations

from typing import Any

IMPLICIT_TOPIC_SWITCH_CASES: list[dict[str, Any]] = [
    {
        "id": "implicit_01",
        "question": "wifi password?",
        "history": [
            {"role": "user", "content": "How many paid leave days can full-time employees carry forward into next year?"},
            {"role": "assistant", "content": "You can carry forward up to 10 days of paid leave into the next calendar year."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "12 characters",
        "any_of": [{"phrase": "12 characters"}, {"phrase": "bitwarden"}],
    },
    {
        "id": "implicit_02",
        "question": "laptop kharab ho gaya",
        "history": [
            {"role": "user", "content": "Resignation dene ke baad confirmed employees ko kitne din ka notice serve karna hota hai?"},
            {"role": "assistant", "content": "Confirmed employees ke liye mandatory separation notice period 60 calendar days hai."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "floor 2, block b",
        "any_of": [{"phrase": "floor 2, block b"}, {"phrase": "it desk"}, {"phrase": "company laptops"}],
    },
    {
        "id": "implicit_03",
        "question": "VPN?",
        "history": [
            {"role": "user", "content": "Can I claim reimbursements for client dinners during travel?"},
            {"role": "assistant", "content": "Yes, meals during travel are reimbursable up to ₹1,500 per day."},
            {"role": "user", "content": "What about flight bookings?"},
            {"role": "assistant", "content": "Domestic travel must be economy class only."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "globalprotect",
    },
    {
        "id": "implicit_04",
        "question": "scren lock?",
        "history": [
            {"role": "user", "content": "Sick leave lene ke liye kitne din pehle manager ko batana padta hai?"},
            {"role": "assistant", "content": "Emergency sick leave absence ke 24 hours ke andar apply karni hoti hai."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "5 minutes",
    },
    {
        "id": "implicit_05",
        "question": "wfh policy kya hai?",
        "history": [
            {"role": "user", "content": "Is Casual Leave carry-forward allowed into the next year?"},
            {"role": "assistant", "content": "No, Casual Leave strictly lapses on 31st December and cannot be carried forward."},
        ],
        "expected_sources": ["employee_handbook.md"],
        "expected_phrase": "2 days per week",
        "any_of": [{"phrase": "2 days per week"}, {"phrase": "core hours"}],
    },
    {
        "id": "implicit_06",
        "question": "aprvd pasword mangr?",
        "history": [
            {"role": "user", "content": "Can new hires work from home during probation?"},
            {"role": "assistant", "content": "No, WFH is not available during the first 90 days or active probation."},
            {"role": "user", "content": "What are the Slack core hours?"},
            {"role": "assistant", "content": "Employees must remain reachable on Slack between 10 AM – 4 PM IST."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "bitwarden",
    },
    {
        "id": "implicit_07",
        "question": "harassment hotline number?",
        "history": [
            {"role": "user", "content": "Company chhodne par laptop aur tokens kahan jama karne hote hain?"},
            {"role": "assistant", "content": "Final working afternoon par Floor 2 IT Service Desk par assets return karne hote hain."},
        ],
        "expected_sources": ["employee_handbook.md"],
        "expected_phrase": "1800-xxx-0001",
    },
    {
        "id": "implicit_08",
        "question": "SLA P1",
        "history": [
            {"role": "user", "content": "How many days of bereavement leave are granted per incident?"},
            {"role": "assistant", "content": "Employees are granted up to 5 consecutive days of bereavement leave."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "2 hours sla",
    },
    {
        "id": "implicit_09",
        "question": "naya software install karna",
        "history": [
            {"role": "user", "content": "Resignation ke baad final payment aane me kitna time lagta hai?"},
            {"role": "assistant", "content": "Full and Final (F&F) settlement exactly 30 calendar days me disburse hota hai."},
            {"role": "user", "content": "Clearance kis kis department se chahiye hoti hai?"},
            {"role": "assistant", "content": "Clearance IT, facilities, aur administration portals se zaroori hai."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "3 business days",
        "any_of": [{"phrase": "3 business days"}, {"phrase": "it.acmecorp.example/request"}],
    },
    {
        "id": "implicit_10",
        "question": "mac encryption?",
        "history": [
            {"role": "user", "content": "How much continuous tenure is required to apply for a sabbatical?"},
            {"role": "assistant", "content": "Sabbatical leave requires a minimum of 5 years of continuous service."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "filevault",
    },
]
