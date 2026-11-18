"""The 15 multi-turn follow-up evaluation cases for Dev set with corrected ground truth."""
from __future__ import annotations

from typing import Any

# 15 Dev follow-up cases (messy, short, typos, Hinglish, 3-turn histories, topic switches)
DEV_15_FOLLOWUPS: list[dict[str, Any]] = [
    {
        "id": "new_mf_01",
        "question": "carry fwd limit?",
        "history": [
            {"role": "user", "content": "Acme Corp me annual leave kitni hoti hai?"},
            {"role": "assistant", "content": "Full-time employees ko 20 days annual paid leave milti hai."},
        ],
        "expected_sources": ["leave_and_notice_policy.md", "employee_handbook.md"],
        "expected_phrase": "maximum of 10 days",
        "any_of": [{"phrase": "maximum of 10 days"}, {"phrase": "10 days max"}, {"phrase": "maximum 10 days"}],
    },
    {
        "id": "new_mf_02",
        "question": "how oftn shuld i chnge it?",
        "history": [
            {"role": "user", "content": "What is the company password complexity policy?"},
            {"role": "assistant", "content": "Passwords must be at least 12 characters and include numbers and special characters."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "90 days",
    },
    {
        "id": "new_mf_03",
        "question": "Wait, actually how do I connect to GlobalProtect VPN from home?",
        "history": [
            {"role": "user", "content": "Can I take bereavement leave?"},
            {"role": "assistant", "content": "Acme Corp provides up to 5 days of paid bereavement leave upon request."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "globalprotect",
    },
    {
        "id": "new_mf_04",
        "question": "aur agar chorna chahein toh kitna notice dena padega?",
        "history": [
            {"role": "user", "content": "Probation period kitna lamba hota hai?"},
            {"role": "assistant", "content": "Standard probation period 90 days hota hai sabhi naye hires ke liye."},
        ],
        "expected_sources": ["leave_and_notice_policy.md", "employee_handbook.md"],
        "expected_phrase": "30 calendar days",
        "any_of": [{"phrase": "30 calendar days"}, {"phrase": "30 days"}, {"phrase": "15 calendar days"}],
    },
    {
        "id": "new_mf_05",
        "question": "Can I take a 2-hour break during them?",
        "history": [
            {"role": "user", "content": "What are the core working hours?"},
            {"role": "assistant", "content": "Core working hours are 10:00 AM to 4:00 PM local time."},
            {"role": "user", "content": "Does this apply to remote employees?"},
            {"role": "assistant", "content": "Yes, all remote employees must remain reachable during core hours."},
        ],
        "expected_sources": ["employee_handbook.md"],
        "expected_phrase": "10 am - 4 pm",
        "any_of": [{"phrase": "10 am - 4 pm"}, {"phrase": "10 am"}, {"phrase": "core hours"}],
    },
    {
        "id": "new_mf_06",
        "question": "doctor ka parcha kab lagana padega?",
        "history": [
            {"role": "user", "content": "Sick leave kitne din milti hai saal me?"},
            {"role": "assistant", "content": "Employees ko har calendar year me 10 days paid sick leave milti hai."},
        ],
        "expected_sources": ["leave_and_notice_policy.md", "employee_handbook.md"],
        "expected_phrase": "consecutive",
        "any_of": [{"phrase": "more than 3 consecutive working days"}, {"phrase": "medical certificate"}],
    },
    {
        "id": "new_mf_07",
        "question": "wat is the sla for p1 crtical?",
        "history": [
            {"role": "user", "content": "How do I report an IT incident?"},
            {"role": "assistant", "content": "Submit a ticket via the IT service portal with the proper severity code."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "2 hours sla",
        "any_of": [{"phrase": "2 hours sla"}, {"phrase": "2 hours"}],
    },
    {
        "id": "new_mf_08",
        "question": "Does the company wipe it when I leave?",
        "history": [
            {"role": "user", "content": "Can I use my personal mobile phone for work email under BYOD?"},
            {"role": "assistant", "content": "Yes, BYOD enrollment requires company MDM profile installation."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "remotely wipe",
    },
    {
        "id": "new_mf_09",
        "question": "Also where do I return my laptop when leaving the company?",
        "history": [
            {"role": "user", "content": "What is the daily meal allowance limit for business travel?"},
            {"role": "assistant", "content": "The daily meal allowance limit is $75 for domestic business travel."},
        ],
        "expected_sources": ["leave_and_notice_policy.md"],
        "expected_phrase": "floor 2 it service desk",
    },
    {
        "id": "new_mf_10",
        "question": "kya notice period buyout possible hai?",
        "history": [
            {"role": "user", "content": "Resignation process kya hai?"},
            {"role": "assistant", "content": "Employee ko HRMS portal par formal resignation notice submit karna hota hai."},
            {"role": "user", "content": "Confirmed staff ka notice period kitne din ka hota hai?"},
            {"role": "assistant", "content": "Confirmed employees ka standard separation notice period 60 calendar days hota hai."},
        ],
        "expected_sources": ["leave_and_notice_policy.md"],
        "expected_phrase": "buyout",
        "any_of": [{"phrase": "buyout"}, {"phrase": "shortened under mutual agreement"}],
    },
    {
        "id": "new_mf_11",
        "question": "Within what timeframe?",
        "history": [
            {"role": "user", "content": "What is the home office setup reimbursement amount for new hires?"},
            {"role": "assistant", "content": "Full-time new hires can claim up to $500 for home office equipment."},
        ],
        "expected_sources": ["employee_handbook.md"],
        "expected_phrase": "30 days",
    },
    {
        "id": "new_mf_12",
        "question": "kya salary milegi is dauran?",
        "history": [
            {"role": "user", "content": "Kya extended sabbatical leave le sakte hain?"},
            {"role": "assistant", "content": "Executive approval ke baad 5 continuous years service wale staff ko 90 days sabbatical mil sakti hai."},
        ],
        "expected_sources": ["leave_and_notice_policy.md"],
        "expected_phrase": "unpaid",
    },
    {
        "id": "new_mf_13",
        "question": "can i request an aditional one?? pls tell process",
        "history": [
            {"role": "user", "content": "How many external monitors does IT supply?"},
            {"role": "assistant", "content": "Standard workstation allocation includes up to two 27-inch monitors."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "it service desk",
        "any_of": [{"phrase": "it service desk"}, {"phrase": "software request"}, {"phrase": "it helpdesk portal"}],
    },
    {
        "id": "new_mf_14",
        "question": "Switching topics: what disk encryption is required on Windows?",
        "history": [
            {"role": "user", "content": "Can a new hire work from home during probation?"},
            {"role": "assistant", "content": "No, WFH is strictly not permitted during the 90-day probation period."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "bitlocker",
    },
    {
        "id": "new_mf_15",
        "question": "kab tak use kar sakte hain?",
        "history": [
            {"role": "user", "content": "Paid leave carry forward kitne din allow hota hai?"},
            {"role": "assistant", "content": "Maximum 10 unused paid leave days carry forward ho sakte hain agle calendar year me."},
        ],
        "expected_sources": ["leave_and_notice_policy.md", "employee_handbook.md"],
        "expected_phrase": "next year",
        "any_of": [{"phrase": "into the next year"}, {"phrase": "next year"}, {"phrase": "next calendar year"}],
    },
]
