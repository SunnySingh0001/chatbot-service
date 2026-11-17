---
source: it_policies.md
department: it
access_level: all
---

# IT Policies

These policies govern the use of Acme Corp's technology resources.
Violations may result in access revocation and disciplinary action.

## Section 2.1: Password Policy

All corporate passwords must be **at least 12 characters** and include uppercase letters,
lowercase letters, digits, and at least one special character (e.g., `!@#$%`).
Passwords must be **rotated every 90 days**. The system will prompt you automatically.
Never share your password with anyone — including IT staff. IT will never ask for your password.
Use the company-approved password manager (**Bitwarden for Teams**) to store credentials.
Reusing the last 5 passwords is blocked by the authentication system.

## Section 3.2: Software Installation

Only software **approved by the IT department** may be installed on company devices.
Submit a software request via the IT helpdesk portal at **it.acmecorp.example/request**.
Requests are reviewed within 3 business days. Approved apps are pushed via MDM.
Unauthorised installations will be removed without notice during the next MDM sync.
Personal software may **not** be installed on company machines under any circumstances.

## Section 5.1: Remote Access and VPN Policy

The corporate VPN (**GlobalProtect**) client must be active whenever accessing internal systems
from outside the office network (home, café, hotel, or public Wi-Fi).
VPN credentials are tied to your corporate email and SSO account.
Do not share VPN credentials or allow others to use your VPN session.
Contact IT at **it@acmecorp.example** or raise a helpdesk ticket for connectivity issues.
Persistent VPN failures should be escalated to your manager and IT within the same working day.

## Section 4.2: Device Security Policy

All company laptops must have **full-disk encryption** enabled (BitLocker on Windows, FileVault on Mac).
Screen lock must activate after **5 minutes of inactivity** — configure this in OS settings.
Report lost or stolen devices to IT within **1 hour** of discovery; IT will remotely wipe the device.
Personal use of company devices is permitted within reasonable limits and must not violate any policy.
Do not use company devices for cryptocurrency mining, gaming, or any illegal activity.

## Section 6.3: Helpdesk SLAs and Support

Raise tickets at **it.acmecorp.example/helpdesk** or email **it@acmecorp.example**.
Service Level Agreements (SLAs) are categorized by priority level:

| Priority Code | Severity Description   | SLA Response Time |
|---------------|------------------------|-------------------|
| SLA P1        | System Down / Outage   | 2 hours SLA       |
| SLA P2        | Major Business Impact  | 4 hours SLA       |
| SLA P3        | Minor Issue / Query    | 1 business day    |

Walk-in IT support is available at the IT desk (Floor 2, Block B) between 9 AM and 6 PM IST.

## Section 7.1: Data Classification and Handling

Data is classified into four levels: **Public, Internal, Confidential, Restricted**.
Confidential and Restricted data must never be stored on personal cloud services
(e.g., personal Google Drive, Dropbox personal).
Use **SharePoint** or **OneDrive for Business** for all company data.
Restricted data may only be handled on encrypted, company-owned devices.
Violations of data handling rules are reported to the Data Protection Officer (DPO).
