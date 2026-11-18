"""Fresh Test Set v2 containing 20 follow-ups verified against stored corpus."""
from __future__ import annotations

from typing import Any

FRESH_20_TEST_CASES_V2: list[dict[str, Any]] = [
    {
        "id": "v2_01",
        "question": "How frequently must they be rotated?",
        "history": [
            {"role": "user", "content": "What is the policy for corporate passwords?"},
            {"role": "assistant", "content": "Passwords must be at least 12 characters and include numbers and symbols."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "every 90 days",
    },
    {
        "id": "v2_02",
        "question": "And for emergency sick leave?",
        "history": [
            {"role": "user", "content": "What is the notice period for taking Paid Leave?"},
            {"role": "assistant", "content": "Taking paid leave requires 2 working days notice."},
        ],
        "expected_sources": ["employee_handbook.md", "leave_and_notice_policy.md"],
        "expected_phrase": "within 24 hours",
    },
    {
        "id": "v2_03",
        "question": "kitne din milti hai ye?",
        "history": [
            {"role": "user", "content": "Acme Corp me casual leave policy kya hai?"},
            {"role": "assistant", "content": "Staff members ko annual casual leave (CL) allocate ki jaati hai."},
        ],
        "expected_sources": ["leave_and_notice_policy.md"],
        "expected_phrase": "6 working days",
        "any_of": [{"phrase": "6 working days"}, {"phrase": "6 days"}],
    },
    {
        "id": "v2_04",
        "question": "उसका क्लेम कितने दिनों के अंदर सबमिट करना होगा?",
        "history": [
            {"role": "user", "content": "कंपनी में होम ऑफिस सेटअप के लिए कितना रिइंबर्समेंट मिलता है?"},
            {"role": "assistant", "content": "फुल-टाइम नए कर्मचारियों को होम ऑफिस सेटअप के लिए $500 तक का क्लेम मिलता है।"},
        ],
        "expected_sources": ["employee_handbook.md"],
        "expected_phrase": "within 30 days",
        "any_of": [{"phrase": "within 30 days"}, {"phrase": "30 days"}],
    },
    {
        "id": "v2_05",
        "question": "Where should I submit a ticket to replace them?",
        "history": [
            {"role": "user", "content": "How many external displays does IT provide?"},
            {"role": "assistant", "content": "Standard workstation allocation includes up to two 27-inch monitors."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "it helpdesk",
        "any_of": [{"phrase": "it helpdesk"}, {"phrase": "helpdesk portal"}, {"phrase": "it service desk"}],
    },
    {
        "id": "v2_06",
        "question": "iska status kis app par active rakhna padega?",
        "history": [
            {"role": "user", "content": "WFH ke dauran kaunse core hours follow karne hote hain?"},
            {"role": "assistant", "content": "Employees ko 10 AM – 4 PM IST ke dauran available rehna hota hai."},
        ],
        "expected_sources": ["employee_handbook.md"],
        "expected_phrase": "slack",
    },
    {
        "id": "v2_07",
        "question": "Does IT remotely wipe it?",
        "history": [
            {"role": "user", "content": "What happens if a company laptop gets stolen?"},
            {"role": "assistant", "content": "A lost or stolen device must be reported to security within 1 hour."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "remotely wipe",
    },
    {
        "id": "v2_08",
        "question": "अगर इस दौरान इस्तीफा दें तो कितने दिन का नोटिस देना होगा?",
        "history": [
            {"role": "user", "content": "कंपनी में प्रोबेशन पीरियड कितना लंबा होता है?"},
            {"role": "assistant", "content": "सभी नए कर्मचारियों के लिए 90 दिन का प्रोबेशन पीरियड होता है।"},
        ],
        "expected_sources": ["leave_and_notice_policy.md"],
        "expected_phrase": "15 calendar days",
    },
    {
        "id": "v2_09",
        "question": "And what about P3 minor queries?",
        "history": [
            {"role": "user", "content": "What is the resolution SLA for P1 outages?"},
            {"role": "assistant", "content": "P1 system down outages carry a 2 hours SLA."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "1 business day",
    },
    {
        "id": "v2_10",
        "question": "kya iske dauran basic salary milti hai?",
        "history": [
            {"role": "user", "content": "5 saal purane staff ko Sabbatical leave mil sakti hai kya?"},
            {"role": "assistant", "content": "Haan, executive committee approval se 90 days sabbatical leave mil sakti hai."},
        ],
        "expected_sources": ["leave_and_notice_policy.md"],
        "expected_phrase": "unpaid",
    },
    {
        "id": "v2_11",
        "question": "Who must approve their WFH requests?",
        "history": [
            {"role": "user", "content": "Can employees work remotely every Friday?"},
            {"role": "assistant", "content": "Employees can work from home up to 2 days per week."},
        ],
        "expected_sources": ["employee_handbook.md"],
        "expected_phrase": "manager",
    },
    {
        "id": "v2_12",
        "question": "लगातार 3 दिन से ज़्यादा बीमार रहने पर इसके लिए क्या डॉक्यूमेंट चाहिए?",
        "history": [
            {"role": "user", "content": "कंपनी में फुल-टाइम कर्मचारियों को साल में कितनी सिक लीव मिलती है?"},
            {"role": "assistant", "content": "कर्मचारियों को प्रति वर्ष 7 दिन की पेड सिक लीव मिलती है।"},
        ],
        "expected_sources": ["employee_handbook.md"],
        "expected_phrase": "medical certificate",
    },
    {
        "id": "v2_13",
        "question": "uska exact desk floor kaunsa hai?",
        "history": [
            {"role": "user", "content": "Resignation ke time hardware handover kaise hota hai?"},
            {"role": "assistant", "content": "Final working day par hardware tokens aur laptop return karne hote hain."},
        ],
        "expected_sources": ["leave_and_notice_policy.md"],
        "expected_phrase": "floor 2 it service desk",
    },
    {
        "id": "v2_14",
        "question": "What percentage is awarded for Band A?",
        "history": [
            {"role": "user", "content": "What are the performance bonus brackets for staff?"},
            {"role": "assistant", "content": "Annual performance bonus percentages depend on the employee's assigned rating band."},
        ],
        "role": "hr",
        "expected_sources": ["hr_benefits.md"],
        "expected_phrase": "15%",
    },
    {
        "id": "v2_15",
        "question": "Completely different question: what is the domestic travel daily meal allowance?",
        "history": [
            {"role": "user", "content": "How complex does my laptop password need to be?"},
            {"role": "assistant", "content": "Passwords must be at least 12 characters with mixed case and numbers."},
        ],
        "expected_sources": ["employee_handbook.md"],
        "expected_phrase": "$75",
    },
    {
        "id": "v2_16",
        "question": "Changing subject: how many minutes of inactivity triggers screen lock?",
        "history": [
            {"role": "user", "content": "Can I carry forward unused sick leave days?"},
            {"role": "assistant", "content": "No, unused sick leave lapses at the end of the calendar year."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "5 minutes",
    },
    {
        "id": "v2_17",
        "question": "Switching topics: what VPN application is approved for corporate access?",
        "history": [
            {"role": "user", "content": "Can I work from home during my 90-day probation?"},
            {"role": "assistant", "content": "No, work from home is not permitted during active probation."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "globalprotect",
    },
    {
        "id": "v2_18",
        "question": "whch passwrd managr is aprvd by it?",
        "history": [
            {"role": "user", "content": "Can we use browser autofill for company passwords?"},
            {"role": "assistant", "content": "No, third party or browser autofill is prohibited."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "bitwarden",
    },
    {
        "id": "v2_19",
        "question": "wat is the requried dsk encrypshun on macOS devics?",
        "history": [
            {"role": "user", "content": "What security software must be installed on personal phones under BYOD?"},
            {"role": "assistant", "content": "Corporate MDM must be installed on BYOD devices."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "filevault",
    },
    {
        "id": "v2_20",
        "question": "within hw mny days mst finnce relse the fnl paymnt?",
        "history": [
            {"role": "user", "content": "Who signs off on employee exit clearance?"},
            {"role": "assistant", "content": "Clearance requires sign-offs from IT, facilities, and administration."},
        ],
        "expected_sources": ["leave_and_notice_policy.md"],
        "expected_phrase": "30 calendar days",
    },
]
