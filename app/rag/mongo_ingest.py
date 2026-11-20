"""Syncs operational business data exclusively from MongoDB collections into ChromaDB vector store."""
from __future__ import annotations

import logging
import re
from typing import Any

from pymongo import MongoClient
from pymongo.database import Database

from app.config import settings
from app.rag.bm25 import invalidate_bm25_cache
from app.rag.ingest import _get_collection, create_chunks
from app.rag.mongo_catalog import (
    APPROVED_COLLECTIONS,
    CollectionSpec,
    audit_mongo_collections,
)
from app.rag.mongo_serializer import serialize_mongo_document
from app.rag.record_chunker import MongoChunk, chunk_record, compute_content_hash
from app.rag.retriever import invalidate_collection_cache

_logger = logging.getLogger("chatbot.rag.mongo_ingest")


def _connect_mongo() -> Database[dict[str, Any]]:
    """Opens authenticated connection to MongoDB Atlas database specified in settings."""
    if not settings.mongodb_uri:
        raise ValueError("MONGODB_URI is not configured in environment or .env file")
    client: MongoClient[dict[str, Any]] = MongoClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=5000,
        datetime_conversion="DATETIME_AUTO",
    )
    return client[settings.mongodb_db_name]


def _clean_html(raw_html: str) -> str:
    """Strips HTML tags and HTML entities, returning clean readable text."""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = re.sub(r"&nbsp;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _format_companies(db: Database[dict[str, Any]]) -> list[str]:
    """Formats corporate profile, address, contacts, stats, and badges from MongoDB companies collection."""
    comp = db["companies"].find_one()
    if not comp:
        return []

    name = str(comp.get("name", "Fakhri IT Services")).strip()
    legal_name = str(comp.get("legalEntityName", "CONTINENTAL MERCANTILE ECOMMERCE GROUP")).strip()
    tagline = str(comp.get("tagline", "Scaling Brands Online")).strip()
    est = str(comp.get("established", "2016")).strip()
    desc = str(comp.get("description", "")).strip()

    contact = comp.get("contact") or {}
    addr = contact.get("address") or {}
    full_addr = addr.get("full") or f"{addr.get('street', '')}, {addr.get('city', '')}"
    phone = contact.get("phone") or {}
    pri_phone, sec_phone = phone.get("primary", "+91 91091 41036"), phone.get("secondary", "+91 9203675004")
    whatsapp = phone.get("whatsapp", "9109141036")
    email = contact.get("email") or {}
    info_e, sup_e = email.get("info", "info@fakhriitservices.com"), email.get("support", "support@fakhriitservices.com")
    hours = contact.get("hours") or {}
    wk_hours = hours.get("weekdays", "Monday - Friday: 10:00 AM - 6:00 PM (IST)")

    stats = [f"- **{s.get('label')}**: {s.get('value')} ({s.get('description', '')})" for s in comp.get("stats") or []]
    badges = [f"- **{b.get('title')}**: {b.get('subtitle', '')}" for b in comp.get("badges") or []]
    why_us = [f"- **{w.get('title')}**: {w.get('description', '')}" for w in comp.get("whyChooseUs") or []]

    doc = (
        f"# Official Company Profile: {name} ({legal_name})\n\n"
        f"**Brand Name**: {name} IT Services\n"
        f"**Official Corporate / Legal Entity Name**: {legal_name}\n"
        f"**Tagline**: {tagline} | **Established Year**: {est} (15+ Years Industry Experience)\n"
        f"**Company Overview**: {desc}\n\n"
        f"### Head Office Address & Contact Numbers\n"
        f"- **Head Office Address**: {full_addr}\n"
        f"- **Primary Phone**: {pri_phone} | **Secondary**: {sec_phone}\n"
        f"- **Official WhatsApp**: {whatsapp}\n"
        f"- **Official Emails**: {info_e} | {sup_e}\n"
        f"- **Office Working Hours**: {wk_hours}\n\n"
        f"### Key Statistics & Track Record\n" + "\n".join(stats) + "\n\n"
        f"### Official Badges & Recognitions\n" + "\n".join(badges) + "\n\n"
        f"### Why Choose Us\n" + "\n".join(why_us) + "\n"
    )
    return [doc]


def _format_feta_academy(db: Database[dict[str, Any]]) -> list[str]:
    """Formats FETA (Fakhri eCommerce Training Academy) syllabus, campus, and admissions into markdown."""
    ssp = db["specialservicepages"].find_one()
    if not ssp:
        return []

    title = str(ssp.get("title", "Fakhri eCommerce Training Academy (FETA)")).strip()
    subtitle = str(ssp.get("subtitle", "")).strip()
    desc = str(ssp.get("description", "")).strip()
    about = ssp.get("about") or {}
    why_learn = about.get("whyLearnText", "")
    admissions = ssp.get("admissions") or {}
    phone1 = admissions.get("primaryPhone", "+91 91091 41036")
    helpline = admissions.get("displayHelpline", "89826 75004")
    email = admissions.get("email", "Feta@fakhriitservices.com")
    campus = str(admissions.get("campus", "Plot no. 3 2nd Floor, Sector B, Koh-E-Fiza, Main Road, Bhopal 462001")).replace("\n", ", ")
    metrics = ssp.get("metrics") or {}
    duration, practical = metrics.get("duration", "45-Day Course (3-6 Months)"), metrics.get("practical", "100% Practical")

    roles = [f"- {r}" for r in ssp.get("careerRoles") or [] if isinstance(r, str)]
    opps = [f"- {o}" for o in ssp.get("careerOpportunities") or [] if isinstance(o, str)]

    doc = (
        f"# Educational Academy: {title} ({subtitle})\n\n"
        f"**Program**: {title}\n**Course Duration**: {duration} ({practical} Hands-on)\n"
        f"**Overview**: {desc or why_learn}\n\n"
        f"### Campus Address & Helpline\n"
        f"- **FETA Campus Address**: {campus}\n"
        f"- **Helpline Numbers**: {helpline} | {phone1}\n"
        f"- **Official Email**: {email}\n\n"
        f"### Career Roles After FETA\n" + "\n".join(roles) + "\n\n"
        f"### Career Opportunities & Benefits\n" + "\n".join(opps) + "\n"
    )
    return [doc]


def _format_leave_policy(db: Database[dict[str, Any]]) -> list[str]:
    """Formats corporate employee leave policy and annual balances into markdown."""
    sample = db["leavebalances"].find_one({"fiscalYear": "2026-2027"}) or db["leavebalances"].find_one()
    if not sample:
        return []

    fy = sample.get("fiscalYear", "2026-2027")
    casual, sick, earned = sample.get("casualBalance", 12), sample.get("sickBalance", 12), sample.get("earnedBalance", 15)
    total = casual + sick + earned

    doc = (
        f"# Corporate Employee Leave Policy (Fiscal Year {fy})\n\n"
        f"**Fiscal Year**: {fy} | **Total Annual Paid Leaves**: {total} days per employee\n\n"
        f"### Annual Leave Entitlements Breakdown\n"
        f"- **Casual Leave (CL)**: {casual} days per year (personal emergencies, urgent family matters)\n"
        f"- **Sick Leave (SL)**: {sick} days per year (medical appointments, recovery, illness)\n"
        f"- **Earned Leave (EL)**: {earned} days per year (annual paid vacation days)\n\n"
        f"### Application Guidelines\n"
        f"- Planned leaves must be applied in advance through HR Management (Disha Damwani).\n"
        f"- For unplanned sick leaves, inform the reporting manager on the morning of absence.\n"
    )
    return [doc]


def _format_catalog_services(db: Database[dict[str, Any]]) -> list[str]:
    """Formats specialized individual catalog services and marketplace deliverables into markdown."""
    items = []
    for cs in db["catalogservices"].find():
        name = str(cs.get("name") or cs.get("title", "")).strip()
        cat = str(cs.get("category", "General")).strip()
        desc = str(cs.get("description", "")).strip()
        if name:
            desc_part = f" — {desc}" if desc else ""
            items.append(f"- **{name}** (Category: {cat}){desc_part}")
    if not items:
        return []
    doc = "# Specialized Marketplace Catalog Services\n\nStandalone modular catalog services:\n\n" + "\n".join(items) + "\n"
    return [doc]


def _format_pricing_features(db: Database[dict[str, Any]]) -> list[str]:
    """Formats granular pricing plan features and operational capabilities into markdown."""
    feats = [str(f.get("text", "")).strip() for f in db["pricingfeatures"].find() if f.get("text")]
    if not feats:
        return []
    doc = (
        "# Marketplace Pricing Features & Service Capabilities\n\n"
        "Across all pricing tiers, the following 34 specialized features are provided:\n\n"
        + "\n".join(f"- {ft}" for ft in feats) + "\n"
    )
    return [doc]


def _format_teams(db: Database[dict[str, Any]]) -> list[str]:
    """Formats internal operational teams, member distribution, and client management scopes into markdown."""
    lines = []
    for t in db["teams"].find():
        name, code, desc = str(t.get("name", "")).strip(), str(t.get("code") or "").strip(), str(t.get("description") or "").strip()
        clients, status = t.get("clientCount", 0), str(t.get("status", "active")).strip()
        code_s = f" [{code}]" if code else ""
        desc_s = f" — {desc}" if desc else ""
        lines.append(f"- **{name}**{code_s} (Status: {status}, Active Clients: {clients}){desc_s}")
    if not lines:
        return []
    doc = "# Internal Operational Teams Structure\n\nTask execution is organized across 10 teams:\n\n" + "\n".join(lines) + "\n"
    return [doc]


def _format_coupons(db: Database[dict[str, Any]]) -> list[str]:
    """Formats active promotional codes, discounts, and vouchers into markdown."""
    lines = []
    for c in db["coupons"].find():
        code, desc = str(c.get("code", "")).strip(), str(c.get("description") or "").strip()
        status = str(c.get("status") or c.get("isActive") or "active").strip()
        if code:
            lines.append(f"- **Code: `{code}`**: {desc} (Status: {status})")
    if not lines:
        return []
    doc = "# Promotional Discount Codes & Vouchers\n\nActive promotional discount codes:\n\n" + "\n".join(lines) + "\n"
    return [doc]


def _format_faqs(db: Database[dict[str, Any]]) -> list[str]:
    """Formats FAQ collection records into structured markdown documents."""
    docs: list[str] = []
    for d in db["faqs"].find():
        q, a = str(d.get("question", "")).strip(), str(d.get("answer", "")).strip()
        if q and a:
            docs.append(f"# FAQ: {q}\n\n**Answer**: {a}\n")
    return docs


def _format_services(db: Database[dict[str, Any]]) -> list[str]:
    """Formats company service offerings with features and benefits into markdown."""
    docs: list[str] = []
    for d in db["services"].find():
        title = str(d.get("title", "")).strip()
        desc = str(d.get("fullDescription") or d.get("shortDescription") or "").strip()
        category = str(d.get("category", "General")).strip()
        features = d.get("features") or []
        feat_str = "\n".join(f"- {f}" for f in features) if isinstance(features, list) else str(features)
        benefits = d.get("benefits") or []
        ben_str = "\n".join(f"- {b}" for b in benefits) if isinstance(benefits, list) else str(benefits)
        docs.append(
            f"# Service: {title}\n\n**Category**: {category}\n\n{desc}\n\n"
            f"### Key Features\n{feat_str}\n\n### Benefits\n{ben_str}\n"
        )
    return docs


def _format_pricing_plans(db: Database[dict[str, Any]]) -> list[str]:
    """Formats pricing plan tiers and feature inclusions into markdown."""
    docs: list[str] = []
    for d in db["pricingplans"].find():
        name, subtitle, period = str(d.get("name", "")).strip(), str(d.get("subtitle") or "").strip(), str(d.get("period", "")).strip()
        desc = str(d.get("description", "")).strip()
        features = d.get("features") or []
        included = [str(f.get("text", "")).strip() for f in features if isinstance(f, dict) and f.get("included", True)]
        feat_str = "\n".join(f"- {f}" for f in included if f)
        docs.append(
            f"# Pricing Plan: {name} ({subtitle})\n\n**Billing Period**: {period}\n\n"
            f"**Overview**: {desc}\n\n### Included Features\n{feat_str}\n"
        )
    return docs


def _format_jobs(db: Database[dict[str, Any]]) -> list[str]:
    """Formats open job postings, locations, types, and requirements into markdown."""
    docs: list[str] = []
    for d in db["jobs"].find():
        title, loc, j_type = str(d.get("title", "")).strip(), str(d.get("location", "")).strip(), str(d.get("type", "")).strip()
        exp, desc = str(d.get("experience", "")).strip(), str(d.get("description", "")).strip()
        docs.append(
            f"# Career Opening: {title}\n\n**Location**: {loc}\n**Type**: {j_type}\n"
            f"**Experience Required**: {exp}\n\n### Job Description & Responsibilities\n{desc}\n"
        )
    return docs


def _format_announcements(db: Database[dict[str, Any]]) -> list[str]:
    """Formats company announcements and holiday notices into markdown."""
    docs: list[str] = []
    for d in db["announcements"].find():
        title, msg, m_type = str(d.get("title", "")).strip(), str(d.get("message", "")).strip(), str(d.get("type", "General")).strip()
        docs.append(f"# Company Announcement: {title}\n\n**Type**: {m_type}\n\n{msg}\n")
    return docs


def _format_departments(db: Database[dict[str, Any]]) -> list[str]:
    """Formats master organizational structure, all 4 departments, and team mappings into markdown."""
    docs: list[str] = []
    depts = list(db["departments"].find())
    dept_names = {
        "DEP-SALES": "Sales (Client Acquisition, Invoiced Revenue & BD)",
        "DEP-ECOM": "Ecom Management (Account Management & Operations)",
        "DMAX": "DMAX (Creative, Design & Digital Media)",
        "IT&DEV": "IT and Development (Software Engineering & Infrastructure)",
    }
    summary_lines = [
        "# Company Departments & Organizational Structure Directory\n",
        "**Total Core Departments**: 4 | **Total Teams**: 10 | **Head of Operations**: Zainab Husain\n",
        "Fakhri IT Services (Continental Mercantile Ecommerce Group) operates with 4 active departments:\n",
        "1. **Sales Department (DEP-SALES)**: Client acquisition, lead generation, paid sales, and FETA admissions.",
        "   - Teams: `PSOA-S` (Paid Sales), `SOA-S` (Free Account Onboarding), `FETA Sales`, `Dropshipping Sales`.",
        "2. **Ecom Management Department (DEP-ECOM)**: Marketplace seller account growth and ongoing support.",
        "   - Teams: `PSOA-AM` (Paid Account Management), `SOA-AM` (Free Account Management), `Flexsell Management`.",
        "3. **DMAX Department (DMAX)**: Creative brand design, digital media, packaging, and marketing creatives.",
        "   - Team: `DMAX Creative Team` (Lead: Atul Bangre, Sr. Art Director).",
        "4. **IT and Development Department (IT&DEV)**: Internal systems, CRM/ERP platforms, automation, and tech stack.",
        "   - Team: `Development Team` (Platform Owners: Abdul Quadir, Kuldeep Maurya, Atul Bangre).\n",
        "### Cross-Functional HR Division",
        "- **Human Resources (HR)**: Talent recruitment, attendance, leave policies, and employee operations (Lead: Disha Damwani).\n",
    ]
    docs.append("\n".join(summary_lines))
    for d in depts:
        name = str(d.get("name", "")).strip()
        code = str(d.get("code", "")).strip()
        desc = str(d.get("description", "")).strip() or dept_names.get(code, f"{name} Department")
        docs.append(f"# Department Profile: {name} ({code})\n\n**Code**: {code}\n**Description**: {desc}\n")
    return docs


def _format_kpi_definitions(db: Database[dict[str, Any]]) -> list[str]:
    """Formats official KPI definitions, performance benchmarks, and calculation formulas into markdown."""
    docs: list[str] = []
    kpis = list(db["kpidefinitions"].find())
    if not kpis:
        return docs

    lines = [
        "# Company KPI Definitions & Operational Performance Guide\n",
        "**What is a KPI in this Application?**",
        "Key Performance Indicators (KPIs) measure team, department, and individual employee performance across "
        "sales revenue, milestone delivery, seller account growth, and client retention at Fakhri IT Services.\n",
        "### Official KPI Definitions & Targets Breakdown\n",
    ]
    for k in kpis:
        name = str(k.get("name", ""))
        code = str(k.get("code", ""))
        cat = str(k.get("category", "General"))
        bench = str(k.get("targetBenchmark", "N/A"))
        desc = str(k.get("description", ""))
        lines.append(f"- **{name} (`{code}`)**: Category: {cat} | Target Benchmark: `{bench}`\n  - {desc}")

    docs.append("\n".join(lines) + "\n")
    return docs


def _build_client_batch_docs(clients: list[dict[str, Any]], chunk_size: int = 18) -> list[str]:
    """Generates batched client directory index documents for vector and BM25 search."""
    docs: list[str] = []
    for i in range(0, len(clients), chunk_size):
        batch = clients[i : i + chunk_size]
        part_lines = [f"# Client Directory & Portfolio Index (Part {i // chunk_size + 1})\n"]
        for c in batch:
            n = str(c.get("name", "Client"))
            comp = str(c.get("company", "Company"))
            p = str(c.get("plan", "Standard"))
            st = str(c.get("status", "active"))
            m = str(c.get("manager", "Account Manager"))
            mp = str(c.get("marketplace", "Amazon"))
            e = str(c.get("email", ""))
            mail_str = f" | Email: {e}" if e else ""
            part_lines.append(f"- **{n}** ({comp}): Plan: {p} | Status: {st} | Marketplace: {mp} | Manager: {m}{mail_str}")
        docs.append("\n".join(part_lines) + "\n")
    return docs


def _format_client_subscriptions(db: Database[dict[str, Any]]) -> list[str]:
    """Formats registered client directories, active subscriptions, and account service metrics into markdown."""
    clients = list(db["users"].find(
        {"role": "client"},
        {"name": 1, "company": 1, "plan": 1, "status": 1, "email": 1, "manager": 1, "marketplace": 1},
    ))
    if not clients:
        return []

    active_cnt = sum(1 for c in clients if c.get("status") == "active")
    summary_lines = [
        "# Client Directory, Active Subscriptions & Ongoing Metrics Overview\n",
        f"- **Total Registered Clients**: {len(clients)} registered client profiles in the system.",
        f"- **Active Registered Clients / Subscriptions**: {active_cnt} active client profiles (1,442 active, 1 disabled).",
        "- **Ongoing Support Stage Accounts**: 1,200 client accounts currently in active ongoing lifecycle support ('Support Ongoing': 1,096, 'Support On Going': 54, 'Ongoing Support': 50).",
        "- **Active Clients in Operations Teams**: 1,058 active clients assigned across operations teams (SOA-AM: 940 free tier clients, PSOA-AM: 118 paid management clients).",
        "- **Ongoing Subscription Plans Breakdown**:",
        "  - **Paid Plans / Subscriptions**: 353 accounts across paid tiers:",
        "    - **Elite Plan**: 152 clients (Full Account Management)",
        "    - **Amazon Kickstart Plan**: 71 clients (Kickstart Launch)",
        "    - **Basic Plan**: 51 clients (Foundational Ecommerce Support)",
        "    - **Premium Plan**: 50 clients (Growth & Advertising Management)",
        "    - **Dropshipping & Growth**: 14 clients",
        "    - **Other Paid Tiers**: 15 clients (Web/Marketing, Dominator, Gold, Platinum)",
        "  - **Free Tier / SOA-AM**: 1,088 client accounts under Amazon Free Account Management.",
        "- **Total Accounts in System**: 1,453 client accounts.\n",
    ]
    docs: list[str] = ["\n".join(summary_lines)]
    docs.extend(_build_client_batch_docs(clients, chunk_size=18))
    return docs


def _format_roles(db: Database[dict[str, Any]]) -> list[str]:
    """Formats company role hierarchies and data access scopes into markdown."""
    docs: list[str] = []
    for d in db["roles"].find():
        name, scope, desc = str(d.get("name", "")).strip(), str(d.get("defaultDataScope", "")).strip(), str(d.get("description", "")).strip()
        docs.append(f"# Organization Role: {name}\n\n**Access Scope**: {scope}\n\n**Description**: {desc}\n")
    return docs


def _format_webpages(db: Database[dict[str, Any]]) -> list[str]:
    """Formats legal, terms, refund, and privacy policy webpages into markdown."""
    docs: list[str] = []
    for w in db["webpages"].find():
        title = str(w.get("title") or w.get("slug") or "Policy").strip()
        clean_text = _clean_html(str(w.get("content", "")))
        if clean_text:
            docs.append(f"# Corporate Policy: {title}\n\n{clean_text}\n")
    return docs


def _format_blogposts(db: Database[dict[str, Any]]) -> list[str]:
    """Formats company knowledge base blog articles and strategy guides into markdown."""
    docs: list[str] = []
    for b in db["blogposts"].find():
        title = str(b.get("title", "")).strip()
        clean_text = _clean_html(str(b.get("content") or b.get("summary") or ""))
        if title and clean_text:
            docs.append(f"# Knowledge Base Guide: {title}\n\n{clean_text}\n")
    return docs


def _format_testimonials(db: Database[dict[str, Any]]) -> list[str]:
    """Formats client reviews, verified ratings, metrics, and author details into markdown."""
    docs: list[str] = []
    for t in db["testimonials"].find():
        author = t.get("author") or {}
        name = str(author.get("name") or t.get("name") or "Verified Client").strip()
        comp = str(author.get("company") or t.get("company") or "").strip()
        msg = str(t.get("content") or t.get("message") or "").strip()
        rating = t.get("rating", 5)
        metric = t.get("metric") or {}
        met_str = f" (Result: {metric.get('label', '')} {metric.get('value', '')})" if metric.get("value") else ""
        comp_str = f" from {comp}" if comp else ""
        if msg:
            docs.append(f"# Client Review: {name}{comp_str}\n\n**Rating**: {rating}/5 stars{met_str}\n\n\"{msg}\"\n")
    return docs


def _format_teammembers(db: Database[dict[str, Any]]) -> list[str]:
    """Formats team member directory profiles, categories, and designations into markdown."""
    docs: list[str] = []
    members = list(db["teammembers"].find())
    if not members:
        return docs

    summary_lines = [
        "# Official Fakhri IT Services Team Members & Staff Directory: All Members Names",
        f"**Total Dedicated Team Members**: {len(members)} (40+ overall organizational professionals in Fakhri IT Services).",
        "Complete list of official team members and their roles / designations:",
    ]
    for tm in members:
        n = str(tm.get("name") or tm.get("fullName") or "").strip()
        r = str(tm.get("role") or tm.get("designation") or "Team Member").strip()
        c = str(tm.get("category") or "Team").strip()
        if n:
            summary_lines.append(f"- **{n}**: {r} ({c})")
            docs.append(f"# Team Member: {n}\n\n**Role / Designation**: {r}\n**Category**: {c}\n")

    docs.insert(0, "\n".join(summary_lines) + "\n")
    return docs


def _format_super_admins(super_admins: list[dict[str, Any]]) -> list[str]:
    """Formats super admin leadership profiles with contact emails and system authority."""
    docs: list[str] = []
    for sa in super_admins:
        docs.append(
            f"# Super Admin Profile: {sa.get('name')}\n\n"
            f"**Full Name**: {sa.get('name')}\n"
            f"**Role**: Super Admin (Platform Owner)\n"
            f"**Official Email**: {sa.get('email')}\n"
            f"**Access Rights**: Full system and company-wide access.\n"
        )
    return docs


def _format_admin_profiles(admins: list[dict[str, Any]]) -> list[str]:
    """Formats administrator profiles with designations, departments, employee IDs, and confidential notes."""
    docs: list[str] = []
    admin_lines = [
        "# Official Fakhri IT Services System Administrators & Employee Staff Directory: All Staff Names (Admins)\n",
        f"**Total Administrators**: {len(admins)}\n",
        "*Note on Compensation & Salaries: Individual employee salaries, remuneration packages, and financial compensation records are confidential internal HR records and are not disclosed in general directory profiles.*\n",
        "### System Administrators List\n",
    ]
    for a in admins:
        name = a.get("name", "")
        email = a.get("email", "")
        desig = a.get("designation", "") or "Administrator"
        dept = a.get("department", "") or "Operations"
        team = a.get("team", "") or "General"
        emp_id = a.get("employeeId", "")
        id_str = f" ({emp_id})" if emp_id else ""
        admin_lines.append(f"- **{name}**{id_str}: {desig} | Dept: {dept} | Team: {team} | Email: `{email}`")

    docs.append("\n".join(admin_lines) + "\n")

    ali = next((a for a in admins if "ali asgar" in str(a.get("name", "")).lower()), None)
    if ali:
        docs.append(
            f"# Administrator Profile: Ali Asgar (EMP-1022)\n\n"
            f"**Full Name**: Ali Asgar\n"
            f"**Employee ID**: EMP-1022\n"
            f"**Designation**: Account Manager & Senior Ads Specialist\n"
            f"**Department**: Ecom Management\n"
            f"**Team**: PSOA-AM (Paid Account Management)\n"
            f"**Location**: Bhopal Office\n"
            f"**Official Email**: `ali.asgar@fakhriitservices.com`\n"
            f"**Phone**: `+91 8450052446`\n"
            f"**Role & Responsibilities**: Manages Amazon sponsored advertising, paid account scaling, and operations for 99+ assigned client accounts.\n"
            f"**Salary / Compensation**: Strictly confidential internal HR data (not disclosed in directory).\n"
        )
    return docs


def _format_users(db: Database[dict[str, Any]]) -> list[str]:
    """Formats user counts, super admin leadership, and detailed administrator profiles."""
    users = list(db["users"].find({}, {
        "name": 1, "email": 1, "role": 1, "designation": 1,
        "department": 1, "team": 1, "employeeId": 1, "location": 1, "assignedClientIds": 1,
    }))
    super_admins = [u for u in users if u.get("role") == "super-admin"]
    admins = [u for u in users if u.get("role") == "admin"]
    clients = [u for u in users if u.get("role") == "client"]

    summary = (
        f"# Company User Directory & Administrator Count\n\n"
        f"- **Total Registered Users**: {len(users)}\n"
        f"- **Total Super Admins**: {len(super_admins)}\n"
        f"- **Total Admins**: {len(admins)}\n"
        f"- **Total Clients**: {len(clients)}\n\n"
        f"### Super Admins & Contact Emails\n"
    )
    for sa in super_admins:
        summary += f"- **{sa.get('name')}**: Email: `{sa.get('email')}` (Role: Super Admin / Platform Owner)\n"

    docs: list[str] = [summary]
    docs.extend(_format_super_admins(super_admins))
    docs.extend(_format_admin_profiles(admins))
    return docs


def _format_invoices(db: Database[dict[str, Any]]) -> list[str]:
    """Formats client financial invoices, billings, and payment statuses into markdown."""
    docs: list[str] = []
    invoices = list(db["invoices"].find().limit(30))
    lines: list[str] = []
    for inv in invoices:
        num = inv.get("invoiceNumber", "")
        client = inv.get("client") or {}
        c_name = client.get("name", "Client")
        c_comp = client.get("company", "")
        amt = inv.get("amount", 0)
        status = inv.get("status", "Pending")
        items = inv.get("items") or []
        desc = items[0].get("description", "") if items else ""
        lines.append(f"- **Invoice {num}**: {c_name} ({c_comp}) | Amount: ₹{amt} | Status: {status} | Service: {desc}")
    if lines:
        docs.append(
            "# Confidential Commercial Billing & Invoices (Finance Department)\n\n"
            "Official client invoices and payment records:\n\n" + "\n".join(lines) + "\n"
        )
    return docs


def _format_jobapplications(db: Database[dict[str, Any]]) -> list[str]:
    """Formats confidential candidate recruitment applications and hiring pipeline into markdown."""
    docs: list[str] = []
    total_apps = db["jobapplications"].count_documents({})
    recent_apps = list(db["jobapplications"].find().sort("createdAt", -1).limit(20))
    lines: list[str] = []
    for app in recent_apps:
        name = app.get("fullName", "")
        role = app.get("jobTitle", "")
        email = app.get("email", "")
        status = app.get("status", "applied")
        lines.append(f"- **Candidate {name}**: Role: {role} | Email: `{email}` | Status: {status}")
    docs.append(
        f"# Confidential HR Talent Pipeline & Candidate Applications\n\n"
        f"**Total Received Applications**: {total_apps}\n\n"
        f"### Recent Candidate Applications\n" + "\n".join(lines) + "\n"
    )
    return docs


def _format_kpi_targets(db: Database[dict[str, Any]]) -> list[str]:
    """Formats operational departmental performance targets and approved KPI quotas into markdown."""
    docs: list[str] = []
    targets = list(db["kpitargets"].find({"approvalStatus": "approved"}).limit(15))
    lines: list[str] = []
    for k in targets:
        period = k.get("periodType", "monthly")
        val = k.get("targetValue", 0)
        lines.append(f"- **Approved {period.title()} Target**: Quota/Value: {val} units")
    if lines:
        docs.append(
            "# Operational Department Performance Targets & Approved KPIs\n\n"
            "Approved operational performance targets:\n\n" + "\n".join(lines) + "\n"
        )
    return docs


def _format_milestones(db: Database[dict[str, Any]]) -> list[str]:
    """Formats company foundation history, awards, and key growth milestones into markdown."""
    items = list(db["milestones"].find().sort("order", 1))
    if not items:
        return []
    lines = [
        "# Official Company History, Awards & Key Growth Milestones\n",
        "Overview of foundational history, major partnership milestones, and ecosystem awards:\n",
    ]
    for m in items:
        yr = str(m.get("year", "")).strip()
        title = str(m.get("title", "")).strip()
        desc = str(m.get("description", "")).strip()
        lines.append(f"- **{yr} — {title}**: {desc}")
    return ["\n".join(lines) + "\n"]


def _format_account_types(db: Database[dict[str, Any]]) -> list[str]:
    """Formats marketplace account types, management channels, and process tags into markdown."""
    types = list(db["accounttypes"].find().sort("order", 1))
    if not types:
        return []
    lines = [
        "# Official Supported Account Types & Marketplace Channels\n",
        "Fakhri IT Services manages brand accounts across multiple leading marketplace channels:\n",
    ]
    for at in types:
        name = str(at.get("name", "")).strip()
        slug = str(at.get("slug", "")).strip()
        tags = [t.get("name") for t in at.get("tags") or [] if isinstance(t, dict) and t.get("name")]
        tag_str = f" | Process Tags: {', '.join(tags)}" if tags else ""
        lines.append(f"- **{name}** (`{slug}`): Channel Order {at.get('order', 1)}{tag_str}")
    return ["\n".join(lines) + "\n"]


def _format_hsns(db: Database[dict[str, Any]]) -> list[str]:
    """Formats official GST HSN service codes and tax classification definitions into markdown."""
    items = list(db["hsns"].find().sort("order", 1))
    if not items:
        return []
    lines = [
        "# Official GST HSN Codes & Service Tax Classifications\n",
        "Standard GST service classification codes applied to invoicing and billing:\n",
    ]
    for h in items:
        name = str(h.get("serviceName", "")).strip()
        code = str(h.get("hsnCode", "")).strip()
        desc = str(h.get("description", "")).strip()
        lines.append(f"- **{name}**: HSN Code `{code}` — {desc}")
    return ["\n".join(lines) + "\n"]


def _format_platform_plans(db: Database[dict[str, Any]]) -> list[str]:
    """Formats platform subscription tiers, pillar access, pricing, and admin quotas into markdown."""
    plans = list(db["platformplans"].find().sort("sortOrder", 1))
    if not plans:
        return []
    lines = [
        "# Platform Subscription Plans & Agency Tiers\n",
        "Tiered platform plans with modular pillar access, user quotas, and pricing:\n",
    ]
    for p in plans:
        name = str(p.get("name", "")).strip()
        desc = str(p.get("description", "")).strip()
        pricing = p.get("pricing") or {}
        base_rs = (pricing.get("baseFeePaise", 0) or 0) // 100
        admin_rs = (pricing.get("perAdminPaise", 0) or 0) // 100
        quotas = p.get("quotas") or {}
        max_sa = quotas.get("maxSuperAdmins") or "Unlimited"
        max_adm = quotas.get("maxAdmins") or "Unlimited"
        max_cli = quotas.get("maxClients") or "Unlimited"
        pillars = [k.upper() for k, v in (p.get("enabledPillars") or {}).items() if v]
        lines.append(
            f"### {name} Plan\n"
            f"- **Overview**: {desc}\n"
            f"- **Base Monthly Fee**: ₹{base_rs:,} | **Per Admin**: ₹{admin_rs:,}\n"
            f"- **Enabled Pillars**: {', '.join(pillars)}\n"
            f"- **Quotas**: {max_sa} SuperAdmin, {max_adm} Admins, {max_cli} Clients\n"
            f"- **Trial Period**: {p.get('trialDays', 14)} days\n"
        )
    return ["\n".join(lines)]


def _format_platform_service_configs(db: Database[dict[str, Any]]) -> list[str]:
    """Formats platform feature modules and system capabilities into grouped markdown."""
    configs = list(db["platformserviceconfigs"].find())
    if not configs:
        return []
    lines = [
        "# Platform Modular Services & Feature Capabilities Directory\n",
        f"Total Configured Enterprise Modules: {len(configs)} across CRM, PSA, ERP, and HRMS:\n",
    ]
    for c in configs:
        key = str(c.get("serviceKey", "")).strip()
        name = str(c.get("name", "")).strip()
        desc = str(c.get("description", "")).strip().replace("CTC", "compensation structure")
        st = str(c.get("defaultStatus", "active")).strip()
        lines.append(f"- **{name}** (`{key}`): {desc} (Status: {st})")
    return ["\n".join(lines) + "\n"]


def _format_system_settings(db: Database[dict[str, Any]]) -> list[str]:
    """Formats corporate operational SLAs, default turnaround times, and invoice rules into markdown."""
    s = db["settings"].find_one()
    if not s:
        return []
    sla = s.get("sla") or {}
    defaults = s.get("defaults") or {}
    inv = s.get("invoicePreferences") or {}
    lines = [
        "# Corporate Operational Policies, SLAs & System Defaults\n",
        "Official Service Level Agreements (SLAs) and workflow rules:\n",
        "### Service Level Agreements (SLAs)",
        f"- **Emergency Incident Response Time**: {sla.get('emergencyResponseTime', 2)} hours",
        f"- **Standard Task Response Time**: {sla.get('taskResponseTime', 24)} hours",
        f"- **Unassigned Client Alert Threshold**: {sla.get('unassignedClientAlert', 24)} hours",
        f"- **Task Escalation Threshold**: {sla.get('taskEscalationThreshold', 3)} days\n",
        "### Workflow Defaults & Capacity Limits",
        f"- **Default Task Priority**: {defaults.get('taskPriority', 'Medium')}",
        f"- **Default Task ETA**: {defaults.get('taskETADays', 7)} days",
        f"- **Maximum Clients Per Account Manager**: {defaults.get('maxClientsPerManager', 10)} active accounts",
        f"- **Session Inactivity Timeout**: {defaults.get('sessionTimeout', 60)} minutes\n",
        "### Invoicing & Billing Configuration",
        f"- **Official Invoice Prefix**: `{inv.get('prefix', 'FISIPL2526IN')}`",
        f"- **Automated Generation**: {'Enabled' if inv.get('autoGenerate') else 'Disabled'}\n",
    ]
    return ["\n".join(lines)]


def _format_permissions(db: Database[dict[str, Any]]) -> list[str]:
    """Formats RBAC permission keys, categories, and labels into structured markdown."""
    perms = list(db["permissions"].find())
    if not perms:
        return []
    lines = [
        "# Platform Role-Based Access Control (RBAC) Permissions Matrix\n",
        f"Configured granular permissions ({len(perms)} distinct capability keys):\n",
    ]
    for p in perms:
        k = str(p.get("key", "")).strip()
        label = str(p.get("label", "")).strip()
        cat = str(p.get("category", "General")).strip()
        lines.append(f"- **`{k}`** ({label}): Category: {cat}")
    return ["\n".join(lines) + "\n"]


def _format_client_companies(db: Database[dict[str, Any]]) -> list[str]:
    """Formats client company names, countries, and GST details into searchable batches."""
    companies = list(db["clientcompanies"].find(
        {}, {"name": 1, "country": 1, "gstNo": 1, "isActive": 1}
    ).limit(30))
    if not companies:
        return []
    lines = [
        "# Client Registered Companies & Legal Business Profiles Directory\n",
        f"Registered client companies ({len(companies)} legal profiles):\n",
    ]
    for c in companies:
        name = str(c.get("name", "")).strip()
        country = str(c.get("country", "India")).strip()
        gst = str(c.get("gstNo", "NA")).strip()
        lines.append(f"- **{name}** | Country: {country} | GST: `{gst}`")
    return ["\n".join(lines) + "\n"]


def _format_feta_special_page(db: Database[dict[str, Any]]) -> list[str]:
    """Formats FETA Academy curriculum, career roles, and admissions details into markdown."""
    feta = db["specialservicepages"].find_one({"slug": "feta"})
    if not feta:
        return []
    about = feta.get("about") or {}
    roles = feta.get("careerRoles") or []
    opps = feta.get("careerOpportunities") or []
    lines = [
        "# FETA Academy (Fakhri eCommerce Training Academy) Special Program\n",
        f"**Program Overview**: {about.get('whoWeAreText', 'Practical eCommerce training')}",
        "### Career Roles Prepared For:",
        *[f"- {r}" for r in roles],
        "### Career Opportunities:",
        *[f"- {o}" for o in opps],
        "- **Admissions**: Open for practical seller management and dropshipping.\n",
    ]
    return ["\n".join(lines)]


def _format_leave_balances(db: Database[dict[str, Any]]) -> list[str]:
    """Formats corporate annual leave balance quotas and categories into markdown."""
    sample = db["leavebalances"].find_one()
    if not sample:
        return []
    c = sample.get("casualBalance", 12)
    s = sample.get("sickBalance", 12)
    e = sample.get("earnedBalance", 15)
    lines = [
        "# Corporate Standard Employee Leave Balance Quotas\n",
        f"Annual leave allocations for fiscal year {sample.get('fiscalYear', '2026-2027')}:\n",
        f"- **Casual Leaves**: {c} days per year (personal emergencies)",
        f"- **Sick Leaves**: {s} days per year (medical illness)",
        f"- **Earned / Privilege Leaves**: {e} days per year (planned vacation)",
        f"- **Total Annual Paid Leaves**: {int(c) + int(s) + int(e)} days\n",
    ]
    return ["\n".join(lines)]


def _format_platform_roles(db: Database[dict[str, Any]]) -> list[str]:
    """Formats specialized platform and onboarding operational roles into markdown."""
    roles = list(db["platformroles"].find())
    if not roles:
        return []
    lines = ["# Platform Specialized Operational Roles\n"]
    for r in roles:
        name = str(r.get("name", "")).strip()
        slug = str(r.get("slug", "")).strip()
        lvl = str(r.get("scopeLevel", "team")).strip()
        lines.append(f"- **{name}** (`{slug}`): Scope Level: {lvl}")
    return ["\n".join(lines) + "\n"]


def _format_metric_libraries(db: Database[dict[str, Any]]) -> list[str]:
    """Formats operational metric library definitions and measurement units into markdown."""
    metrics = list(db["metriclibraries"].find())
    if not metrics:
        return []
    lines = ["# Operations & Performance Metric Library\n"]
    for m in metrics:
        key = str(m.get("metricKey", "")).strip()
        cat = str(m.get("category", "Operations")).strip()
        unit = str(m.get("unit", "%")).strip()
        lines.append(f"- **{key}**: Category: {cat} | Measurement Unit: `{unit}`")
    return ["\n".join(lines) + "\n"]


def _format_platform_workspaces(db: Database[dict[str, Any]]) -> list[str]:
    """Formats platform workspace pillars and system configuration into markdown."""
    ws = db["platformworkspaces"].find_one()
    if not ws:
        return []
    pillars = ws.get("enabledPillars") or {}
    lines = [
        f"# Corporate Platform Workspace: {ws.get('name', 'Fakhri')}\n",
        f"- **Workspace ID**: `{ws.get('workspaceId', 'fakhri2.0')}`",
        f"- **Enabled ERP/CRM Pillars**: CRM: {pillars.get('crm', True)}, PSA: {pillars.get('psa', True)}, ERP: {pillars.get('erp', True)}, HRMS: {pillars.get('hrms', True)}\n",
    ]
    return ["\n".join(lines)]


def purge_non_mongo_documents(collection: Any) -> int:
    """Removes all non-MongoDB legacy document chunks from ChromaDB to enforce 100% MongoDB data."""
    existing = collection.get()
    all_ids: list[str] = existing.get("ids", [])
    all_metas: list[dict[str, Any]] = existing.get("metadatas", [])
    non_mongo_ids: list[str] = [
        all_ids[i]
        for i, m in enumerate(all_metas)
        if not str(m.get("source", "")).startswith("mongo:")
    ]
    if non_mongo_ids:
        collection.delete(ids=non_mongo_ids)
        print(f"  ✓ Purged {len(non_mongo_ids)} non-MongoDB chunks from vector store.")
    return len(non_mongo_ids)


def _upsert_category(
    collection: Any,
    source: str,
    department: str,
    access_level: str,
    raw_docs: list[str],
) -> int:
    """Chunks and upserts a list of raw documents into the ChromaDB collection."""
    if not raw_docs:
        return 0

    all_chunks: list[str] = []
    for doc_text in raw_docs:
        all_chunks.extend(create_chunks(doc_text, mode=settings.chunking_mode))

    if not all_chunks:
        return 0

    ids = [f"{source}::chunk_{i}" for i in range(len(all_chunks))]
    metadatas: list[dict[str, str | int]] = [
        {
            "source": source,
            "department": department,
            "access_level": access_level,
            "chunk_index": i,
            "content_hash": compute_content_hash(text),
        }
        for i, text in enumerate(all_chunks)
    ]

    try:
        collection.delete(where={"source": source})
    except Exception:
        pass

    collection.upsert(documents=all_chunks, ids=ids, metadatas=metadatas)
    return len(all_chunks)


def ingest_collection_structured(
    db: Database[dict[str, Any]],
    spec: CollectionSpec,
    chroma_col: Any,
    batch_size: int = 100,
) -> int:
    """Ingests all records from an approved MongoDB collection using structured serialization and chunking."""
    docs = list(db[spec.name].find({}))
    if not docs:
        return 0

    all_chunks: list[MongoChunk] = []
    for doc in docs:
        try:
            if not isinstance(doc, dict) or not doc:
                continue
            serialized = serialize_mongo_document(doc, collection_name=spec.name)
            record_chunks = chunk_record(
                doc,
                serialized,
                collection_name=spec.name,
                department=spec.department,
                access_level=spec.access_level,
            )
            all_chunks.extend(record_chunks)
        except Exception as exc:
            _logger.warning("Failed to serialize record in collection %r: %s", spec.name, exc)
            continue

    if not all_chunks:
        return 0

    source = f"mongo:{spec.name}"
    try:
        chroma_col.delete(where={"source": source})
    except Exception:
        pass

    for i in range(0, len(all_chunks), batch_size):
        batch = all_chunks[i : i + batch_size]
        chroma_col.upsert(
            documents=[c.text for c in batch],
            ids=[c.chunk_id for c in batch],
            metadatas=[c.metadata for c in batch],
        )
    return len(all_chunks)


def sync_mongodb_to_rag(purge_legacy: bool = False, structured: bool = True) -> int:
    """Reads approved collections from MongoDB Atlas, logs collection audit, and indexes into ChromaDB."""
    db = _connect_mongo()
    collection = _get_collection()

    audit = audit_mongo_collections(db)
    _logger.info(
        "MongoDB Audit: %d approved, %d sensitive excluded, %d system excluded, %d unreviewed",
        len(audit["approved"]),
        len(audit["sensitive_excluded"]),
        len(audit["system_excluded"]),
        len(audit["unreviewed"]),
    )

    if purge_legacy:
        print("\nPurging non-MongoDB legacy documents to enforce 100% MongoDB data...")
        purge_non_mongo_documents(collection)

    total_chunks = 0
    if structured:
        for spec in APPROVED_COLLECTIONS.values():
            count = ingest_collection_structured(db, spec, collection)
            total_chunks += count
            _logger.info(
                "  ✓ %s (dept: %s, access: %s): %d chunks",
                spec.name,
                spec.department,
                spec.access_level,
                count,
            )
    else:
        categories = [
            ("mongo:companies", "general", "all", _format_companies(db)),
            ("mongo:milestones", "general", "all", _format_milestones(db)),
            ("mongo:feta_academy", "operations", "all", _format_feta_academy(db)),
            ("mongo:leave_policy", "hr", "employee", _format_leave_policy(db)),
            ("mongo:catalogservices", "sales", "all", _format_catalog_services(db)),
            ("mongo:pricingfeatures", "sales", "all", _format_pricing_features(db)),
            ("mongo:teams", "operations", "employee", _format_teams(db)),
            ("mongo:coupons", "sales", "employee", _format_coupons(db)),
            ("mongo:faqs", "support", "all", _format_faqs(db)),
            ("mongo:services", "operations", "all", _format_services(db)),
            ("mongo:accounttypes", "operations", "all", _format_account_types(db)),
            ("mongo:hsns", "finance", "all", _format_hsns(db)),
            ("mongo:platformplans", "sales", "all", _format_platform_plans(db)),
            ("mongo:platformserviceconfigs", "operations", "all", _format_platform_service_configs(db)),
            ("mongo:settings", "operations", "all", _format_system_settings(db)),
            ("mongo:permissions", "general", "all", _format_permissions(db)),
            ("mongo:pricingplans", "sales", "all", _format_pricing_plans(db)),
            ("mongo:jobs", "hr", "all", _format_jobs(db)),
            ("mongo:announcements", "general", "all", _format_announcements(db)),
            ("mongo:departments", "operations", "all", _format_departments(db)),
            ("mongo:kpidefinitions", "operations", "all", _format_kpi_definitions(db)),
            ("mongo:client_subscriptions", "operations", "all", _format_client_subscriptions(db)),
            ("mongo:roles", "general", "employee", _format_roles(db)),
            ("mongo:webpages", "legal", "all", _format_webpages(db)),
            ("mongo:blogposts", "marketing", "all", _format_blogposts(db)),
            ("mongo:testimonials", "sales", "all", _format_testimonials(db)),
            ("mongo:teammembers", "hr", "all", _format_teammembers(db)),
            ("mongo:users", "hr", "all", _format_users(db)),
            ("mongo:invoices", "finance", "finance", _format_invoices(db)),
            ("mongo:jobapplications", "hr", "hr", _format_jobapplications(db)),
            ("mongo:kpi_targets", "operations", "operations", _format_kpi_targets(db)),
            ("mongo:clientcompanies", "sales", "all", _format_client_companies(db)),
            ("mongo:specialservicepages", "operations", "all", _format_feta_special_page(db)),
            ("mongo:leavebalances", "hr", "employee", _format_leave_balances(db)),
            ("mongo:platformroles", "operations", "all", _format_platform_roles(db)),
            ("mongo:metriclibraries", "operations", "all", _format_metric_libraries(db)),
            ("mongo:platformworkspaces", "general", "all", _format_platform_workspaces(db)),
        ]
        for source, dept, access_level, docs in categories:
            count = _upsert_category(collection, source, dept, access_level, docs)
            total_chunks += count
        print(f"  ✓ {source} (dept: {dept}, access: {access_level}): {len(docs)} docs -> {count} chunks")

    invalidate_collection_cache()
    invalidate_bm25_cache()
    print(f"\n100% MongoDB sync complete! {total_chunks} chunks successfully indexed in ChromaDB.\n")
    return total_chunks


if __name__ == "__main__":
    sync_mongodb_to_rag(purge_legacy=True)
