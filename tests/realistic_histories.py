"""Twelve realistic multi-turn conversation test cases with distant or assistant-only topic nouns."""
from __future__ import annotations

from typing import Any

# 12 realistic multi-turn cases (4 to 6 turns, mixed English/Hindi/Hinglish/Devanagari)
# Topic words appear ONLY in assistant messages or >=3 turns before the follow-up.
REALISTIC_12_HISTORIES: list[dict[str, Any]] = [
    {
        "id": "real_01",
        "question": "aur ye agle saal carry forward hoti hai kya?",
        "history": [
            {"role": "user", "content": "Acme Corp me joining ke baad personal emergencies ke liye kaunsi chhutti milti hai?"},
            {"role": "assistant", "content": "Personal emergencies ke liye employees ko Casual Leave (CL) allocate ki jaati hai."},
            {"role": "user", "content": "Theek hai, samajh gaya."},
            {"role": "assistant", "content": "Haan, aur iske liye koi lamba advance approval nahi chahiye hota."},
        ],
        "expected_sources": ["leave_and_notice_policy.md"],
        "expected_phrase": "strictly lapses on 31st december",
        "any_of": [{"phrase": "strictly lapses on 31st december"}, {"phrase": "strictly lapses"}, {"phrase": "0 days"}],
    },
    {
        "id": "real_02",
        "question": "If it fails persistently, who should I escalate to on the same working day?",
        "history": [
            {"role": "user", "content": "I had a question regarding our corporate VPN client configuration."},
            {"role": "assistant", "content": "Sure, our corporate network uses the GlobalProtect VPN client for remote access."},
            {"role": "user", "content": "I am having some connection drops today."},
            {"role": "assistant", "content": "Please verify your SSO credentials and restart your client."},
            {"role": "user", "content": "Okay, tried that and it is stable now."},
            {"role": "assistant", "content": "Great, let me know if you need any other assistance."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "manager and it",
        "any_of": [{"phrase": "manager and it"}, {"phrase": "escalated to your manager"}],
    },
    {
        "id": "real_03",
        "question": "प्रोबेशन पीरियड के दौरान क्या इसकी अनुमति है?",
        "history": [
            {"role": "user", "content": "ऑफिस आने-जाने के लिए कोई विशेष नियम हैं क्या?"},
            {"role": "assistant", "content": "कंपनी में हाइब्रिड मॉडल लागू है जिसमें कर्मचारी Work from Home (WFH) सुविधा का लाभ ले सकते हैं।"},
            {"role": "user", "content": "अच्छा, धन्यवाद।"},
            {"role": "assistant", "content": "अगर आप इस बारे में कुछ और जानना चाहते हैं तो बताइए।"},
        ],
        "expected_sources": ["employee_handbook.md"],
        "expected_phrase": "active probation",
        "any_of": [{"phrase": "active probation"}, {"phrase": "first 90 days"}, {"phrase": "not available during the first 90 days"}],
    },
    {
        "id": "real_04",
        "question": "How many minutes of inactivity triggers it?",
        "history": [
            {"role": "user", "content": "I noticed my workstation locked itself when I stepped away."},
            {"role": "assistant", "content": "Yes, our device security policy enforces automatic screen lock across all laptops."},
            {"role": "user", "content": "Got it, that makes sense."},
            {"role": "assistant", "content": "You can check this under your system preferences."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "5 minutes of inactivity",
        "any_of": [{"phrase": "5 minutes of inactivity"}, {"phrase": "5 minutes"}],
    },
    {
        "id": "real_05",
        "question": "Isko lene ke liye kitne saal ki continuous service mandatory hai?",
        "history": [
            {"role": "user", "content": "Sabbatical Leave ke baare me janna tha."},
            {"role": "assistant", "content": "Sabbatical Leave unpaid leave hoti hai jo extended personal ya educational purposes ke liye milti hai."},
            {"role": "user", "content": "Ye maximum kitne dino ke liye mil sakti hai?"},
            {"role": "assistant", "content": "Policy ke according maximum 90 days unpaid sabbatical allow hota hai."},
            {"role": "user", "content": "Acha theek hai."},
            {"role": "assistant", "content": "Iske liye formal approval process follow karna padta hai."},
        ],
        "expected_sources": ["leave_and_notice_policy.md"],
        "expected_phrase": "5 years continuous service",
    },
    {
        "id": "real_06",
        "question": "Submit karne ke baad kitne business days me request review hoti hai?",
        "history": [
            {"role": "user", "content": "Main apne team ke liye ek naya open-source tool download karna chahta hoon."},
            {"role": "assistant", "content": "Dhayan rahe ki company devices par sirf approved Software Installation allowed hai."},
            {"role": "user", "content": "Samajh gaya, par iski request kaise daalte hain?"},
            {"role": "assistant", "content": "Aap IT helpdesk portal par software request submit kar sakte hain."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "3 business days",
    },
    {
        "id": "real_07",
        "question": "इन नियमों का उल्लंघन होने पर किसकी रिपोर्ट की जाती है?",
        "history": [
            {"role": "user", "content": "कंपनी के संवेदनशील डेटा को स्टोर करने के क्या दिशा-निर्देश हैं?"},
            {"role": "assistant", "content": "डेटा क्लासिफिकेशन नियमों के अनुसार गोपनीय फाइलों के लिए SharePoint और OneDrive for Business का उपयोग अनिवार्य है।"},
            {"role": "user", "content": "ठीक है, मैं इसका ध्यान रखूँगा।"},
            {"role": "assistant", "content": "याद रखें कि पर्सनल गूगल ड्राइव का उपयोग पूरी तरह प्रतिबंधित है।"},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "data protection officer",
        "any_of": [{"phrase": "data protection officer"}, {"phrase": "dpo"}],
    },
    {
        "id": "real_08",
        "question": "What is the deadline in days to submit the claim after incurring it?",
        "history": [
            {"role": "user", "content": "Could you explain the process for submitting business expense claims?"},
            {"role": "assistant", "content": "Business expense reimbursements must be submitted through the finance portal with supporting invoices."},
            {"role": "user", "content": "I had some client dinners last week during travel."},
            {"role": "assistant", "content": "Client meals are reimbursable subject to per diem policy limits."},
            {"role": "user", "content": "All receipts are compiled in a PDF."},
            {"role": "assistant", "content": "Ensure you attach the detailed itemized bill before submitting."},
        ],
        "expected_sources": ["employee_handbook.md"],
        "expected_phrase": "within 30 days",
        "any_of": [{"phrase": "within 30 days"}, {"phrase": "30 days"}],
    },
    {
        "id": "real_09",
        "question": "Saare clearances hone ke baad F&F settlement kitne calendar days me release hota hai?",
        "history": [
            {"role": "user", "content": "Maine kal resignation submit kiya tha system me."},
            {"role": "assistant", "content": "Resignation ke baad company Standard Operational Procedure for Employee Exit Clearance follow karna padta hai."},
            {"role": "user", "content": "Haan mujhe mail aaya tha HR se."},
            {"role": "assistant", "content": "Aapko IT hardware and smartcards Floor 2 service desk par jama karne honge."},
        ],
        "expected_sources": ["leave_and_notice_policy.md"],
        "expected_phrase": "30 calendar days",
        "any_of": [{"phrase": "30 calendar days"}, {"phrase": "exactly 30 calendar days"}],
    },
    {
        "id": "real_10",
        "question": "What is the mandatory response time SLA for P1 outages?",
        "history": [
            {"role": "user", "content": "What are the priority tiers in our IT Helpdesk SLAs?"},
            {"role": "assistant", "content": "Our IT Helpdesk SLAs are categorized into SLA P1, P2, and P3."},
            {"role": "user", "content": "We had a total server crash this morning."},
            {"role": "assistant", "content": "A total server crash is treated as a P1 System Down outage."},
            {"role": "user", "content": "Our engineers are currently investigating the root cause."},
            {"role": "assistant", "content": "Understood, IT operations has been notified."},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "2 hours sla",
    },
    {
        "id": "real_11",
        "question": "यदि कंपनी का लैपटॉप खो जाए या चोरी हो जाए, तो कितने समय में रिपोर्ट करना अनिवार्य है?",
        "history": [
            {"role": "user", "content": "मैं अपने पर्सनल लैपटॉप पर ऑफिस का काम करना चाहता हूँ।"},
            {"role": "assistant", "content": "कंपनी की नीति के अनुसार केवल आधिकारिक डिवाइस पर BitLocker और FileVault जैसे Full-disk encryption के साथ काम करने की अनुमति है।"},
            {"role": "user", "content": "अच्छा, मुझे नहीं पता था।"},
            {"role": "assistant", "content": "सुरक्षा कारणों से पर्सनल डिवाइस पर आंतरिक डेटा रखना प्रतिबंधित है।"},
        ],
        "expected_sources": ["it_policies.md"],
        "expected_phrase": "within 1 hour",
        "any_of": [{"phrase": "within 1 hour"}, {"phrase": "1 hour"}],
    },
    {
        "id": "real_12",
        "question": "Bache ki maximum age kitni honi chahiye is entitlement ke liye?",
        "history": [
            {"role": "user", "content": "Kya company me bache ke adoption ke liye koi chutti milti hai?"},
            {"role": "assistant", "content": "Haan, Acme Corp leave framework me Adoption Leave (AL) provide ki jaati hai."},
            {"role": "user", "content": "Accha, ye policy me certified hai?"},
            {"role": "assistant", "content": "Ji haan, HR Operations lead iski eligibility verify karte hain."},
        ],
        "expected_sources": ["leave_and_notice_policy.md"],
        "expected_phrase": "under 1 year of age",
    },
]
