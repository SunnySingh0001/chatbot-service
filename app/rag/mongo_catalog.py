"""Centralized MongoDB collection catalog, governance boundaries, and access specifications."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pymongo.database import Database


@dataclass(frozen=True)
class CollectionSpec:
    """Governance and ingestion specification for an approved MongoDB collection."""

    name: str
    department: str
    access_level: str
    title_field: str = "title"
    excluded_fields: frozenset[str] = field(default_factory=frozenset)


# ── Approved Knowledge & Operational Collections ─────────────────────────────
APPROVED_COLLECTIONS: dict[str, CollectionSpec] = {
    # ── Corporate, Policies & Foundation ──
    "companies": CollectionSpec("companies", "general", "all", title_field="name"),
    "milestones": CollectionSpec("milestones", "general", "all", title_field="title"),
    "departments": CollectionSpec("departments", "operations", "all", title_field="name"),
    "webpages": CollectionSpec("webpages", "legal", "all", title_field="title"),
    "announcements": CollectionSpec("announcements", "general", "all", title_field="title"),
    "settings": CollectionSpec("settings", "operations", "employee", title_field="key"),
    "permissions": CollectionSpec("permissions", "general", "employee", title_field="name"),
    "roles": CollectionSpec("roles", "general", "employee", title_field="name"),
    "platformroles": CollectionSpec("platformroles", "operations", "employee", title_field="name"),
    "platformworkspaces": CollectionSpec("platformworkspaces", "general", "employee", title_field="name"),
    # ── Operations, Tasks & Projects ──
    "tasks": CollectionSpec("tasks", "operations", "employee", title_field="title"),
    "approvalrequests": CollectionSpec("approvalrequests", "operations", "operations", title_field="title"),
    "notes": CollectionSpec("notes", "operations", "employee", title_field="title"),
    "services": CollectionSpec("services", "operations", "all", title_field="title"),
    "specialservicepages": CollectionSpec("specialservicepages", "operations", "all", title_field="title"),
    "servicetemplates": CollectionSpec("servicetemplates", "operations", "operations", title_field="title"),
    "platformserviceconfigs": CollectionSpec("platformserviceconfigs", "operations", "all", title_field="serviceName"),
    "soptemplates": CollectionSpec("soptemplates", "operations", "employee", title_field="title"),
    "clientsettings": CollectionSpec("clientsettings", "operations", "operations", title_field="title"),
    "orgmemberships": CollectionSpec("orgmemberships", "operations", "employee", title_field="title"),
    "orgversions": CollectionSpec("orgversions", "operations", "employee", title_field="title"),
    # ── Commercial, Sales, Clients & Leads ──
    "clientcompanies": CollectionSpec("clientcompanies", "sales", "employee", title_field="name"),
    "clientaccounts": CollectionSpec("clientaccounts", "sales", "employee", title_field="marketplace"),
    "leads": CollectionSpec("leads", "sales", "employee", title_field="name"),
    "contacts": CollectionSpec("contacts", "sales", "employee", title_field="name"),
    "contracts": CollectionSpec("contracts", "sales", "employee", title_field="contractNumber"),
    "catalogservices": CollectionSpec("catalogservices", "sales", "all", title_field="name"),
    "pricingplans": CollectionSpec("pricingplans", "sales", "all", title_field="name"),
    "pricingfeatures": CollectionSpec("pricingfeatures", "sales", "all", title_field="title"),
    "platformplans": CollectionSpec("platformplans", "sales", "all", title_field="name"),
    "coupons": CollectionSpec("coupons", "sales", "employee", title_field="code"),
    "testimonials": CollectionSpec("testimonials", "sales", "all", title_field="name"),
    "subscriptionevents": CollectionSpec("subscriptionevents", "sales", "employee", title_field="event"),
    "flexsellattributions": CollectionSpec("flexsellattributions", "sales", "employee", title_field="title"),
    # ── HR, People & Teams ──
    "users": CollectionSpec("users", "hr", "employee", title_field="name"),
    "teams": CollectionSpec("teams", "operations", "employee", title_field="name"),
    "teammembers": CollectionSpec("teammembers", "hr", "employee", title_field="name"),
    "jobs": CollectionSpec("jobs", "hr", "all", title_field="title"),
    "jobapplications": CollectionSpec("jobapplications", "hr", "hr", title_field="fullName"),
    "leavebalances": CollectionSpec("leavebalances", "hr", "employee", title_field="fiscalYear"),
    "reportingrelations": CollectionSpec("reportingrelations", "hr", "employee", title_field="title"),
    "profileupdaterequests": CollectionSpec("profileupdaterequests", "hr", "hr", title_field="title"),
    "orgroletemplates": CollectionSpec("orgroletemplates", "hr", "employee", title_field="title"),
    # ── Performance, KPIs & Metrics ──
    "kpidefinitions": CollectionSpec("kpidefinitions", "operations", "employee", title_field="name"),
    "kpitargets": CollectionSpec("kpitargets", "operations", "operations", title_field="targetCode"),
    "kpiassignments": CollectionSpec("kpiassignments", "operations", "operations", title_field="title"),
    "kpiperformancerecords": CollectionSpec("kpiperformancerecords", "operations", "operations", title_field="title"),
    "kpiperiods": CollectionSpec("kpiperiods", "operations", "operations", title_field="title"),
    "metriclibraries": CollectionSpec("metriclibraries", "operations", "employee", title_field="metricName"),
    "scoresnapshots": CollectionSpec("scoresnapshots", "operations", "operations", title_field="title"),
    # ── Finance & Billing ──
    "invoices": CollectionSpec("invoices", "finance", "finance", title_field="invoiceNumber"),
    "hsns": CollectionSpec("hsns", "finance", "all", title_field="hsnCode"),
    "accounttypes": CollectionSpec("accounttypes", "operations", "employee", title_field="name"),
    # ── Support & Marketing ──
    "faqs": CollectionSpec("faqs", "support", "all", title_field="question"),
    "supporttickets": CollectionSpec("supporttickets", "support", "employee", title_field="subject"),
    "feedbacks": CollectionSpec("feedbacks", "support", "employee", title_field="title"),
    "npssurveys": CollectionSpec("npssurveys", "support", "employee", title_field="title"),
    "blogposts": CollectionSpec("blogposts", "marketing", "all", title_field="title"),
    "emailcampaigns": CollectionSpec("emailcampaigns", "marketing", "employee", title_field="name"),
    # ── HR & Employee Workflow ──
    "attendances": CollectionSpec("attendances", "hr", "employee", title_field="date"),
    "employeedocuments": CollectionSpec("employeedocuments", "hr", "hr", title_field="name"),
    "leaverequests": CollectionSpec("leaverequests", "hr", "employee", title_field="leaveType"),
    # ── Operations & Delivery ──
    "clientsignoffs": CollectionSpec("clientsignoffs", "operations", "operations", title_field="title"),
    "emailtemplates": CollectionSpec("emailtemplates", "operations", "employee", title_field="name"),
    "kpicorrections": CollectionSpec("kpicorrections", "operations", "operations", title_field="title"),
    "reportdefinitions": CollectionSpec("reportdefinitions", "operations", "operations", title_field="name"),
    "resourcecapacities": CollectionSpec("resourcecapacities", "operations", "operations", title_field="title"),
    # ── Sales & Platform Subscriptions ──
    "campaignintegrations": CollectionSpec("campaignintegrations", "sales", "employee", title_field="name"),
    "platformsubscriptions": CollectionSpec("platformsubscriptions", "sales", "all", title_field="name"),
    "subscriptions": CollectionSpec("subscriptions", "sales", "all", title_field="name"),
}

# ── Explicitly Excluded Sensitive Collections (Blocked from Vector Indexing) ──
SENSITIVE_EXCLUDED_COLLECTIONS: frozenset[str] = frozenset({
    "otps", "salaryslips", "salaryadvances", "exitsettlements",
    "banktransactions", "ledgeraccounts", "ledgerjournals", "vendorbills",
    "expenses", "paymentorders", "ssosigningkeys", "ssoauthorizationcodes",
    "ssoauditlogs", "connectedapps",
})

# ── Operational, Telemetry, and Binary Storage Collections ───────────────────
SYSTEM_EXCLUDED_COLLECTIONS: frozenset[str] = frozenset({
    "cronstates", "ratelimits", "proofUploads.chunks", "proofUploads.files",
    "files", "chatmessages", "chatrooms", "chat_sessions", "activitylogs",
    "activitylogs_archive", "emaillogs", "emailsuppressions", "analytics",
    "notifications",
    "gst_reconcile_snapshots", "placementsnapshots", "placementsnapshotrows",
    "webhookevents",
})


def is_system_snapshot(name: str) -> bool:
    """Checks whether a collection name matches automated migration or snapshot patterns."""
    return "_snapshot" in name or name.startswith("system.")


def audit_mongo_collections(db: Database[dict[str, Any]]) -> dict[str, list[str]]:
    """Audits all collections in the MongoDB database and classifies them by governance category."""
    all_names = set(db.list_collection_names())
    approved: list[str] = []
    sensitive: list[str] = []
    system: list[str] = []
    unreviewed: list[str] = []

    for name in sorted(all_names):
        if is_system_snapshot(name) or name in SYSTEM_EXCLUDED_COLLECTIONS:
            system.append(name)
        elif name in SENSITIVE_EXCLUDED_COLLECTIONS:
            sensitive.append(name)
        elif name in APPROVED_COLLECTIONS:
            approved.append(name)
        else:
            unreviewed.append(name)

    return {
        "approved": approved,
        "sensitive_excluded": sensitive,
        "system_excluded": system,
        "unreviewed": unreviewed,
    }
