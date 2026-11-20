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
    "companies": CollectionSpec("companies", "general", "all", title_field="name"),
    "milestones": CollectionSpec("milestones", "general", "all", title_field="title"),
    "specialservicepages": CollectionSpec("specialservicepages", "operations", "all", title_field="title"),
    "catalogservices": CollectionSpec("catalogservices", "sales", "all", title_field="name"),
    "pricingfeatures": CollectionSpec("pricingfeatures", "sales", "all", title_field="title"),
    "teams": CollectionSpec("teams", "operations", "employee", title_field="name"),
    "coupons": CollectionSpec("coupons", "sales", "employee", title_field="code"),
    "faqs": CollectionSpec("faqs", "support", "all", title_field="question"),
    "services": CollectionSpec("services", "operations", "all", title_field="title"),
    "accounttypes": CollectionSpec("accounttypes", "operations", "all", title_field="name"),
    "hsns": CollectionSpec("hsns", "finance", "all", title_field="hsnCode"),
    "platformplans": CollectionSpec("platformplans", "sales", "all", title_field="name"),
    "platformserviceconfigs": CollectionSpec("platformserviceconfigs", "operations", "all", title_field="serviceName"),
    "settings": CollectionSpec("settings", "operations", "all", title_field="key"),
    "permissions": CollectionSpec("permissions", "general", "all", title_field="name"),
    "pricingplans": CollectionSpec("pricingplans", "sales", "all", title_field="name"),
    "jobs": CollectionSpec("jobs", "hr", "all", title_field="title"),
    "announcements": CollectionSpec("announcements", "general", "all", title_field="title"),
    "departments": CollectionSpec("departments", "operations", "all", title_field="name"),
    "kpidefinitions": CollectionSpec("kpidefinitions", "operations", "all", title_field="name"),
    "clientcompanies": CollectionSpec("clientcompanies", "sales", "all", title_field="name"),
    "roles": CollectionSpec("roles", "general", "employee", title_field="name"),
    "webpages": CollectionSpec("webpages", "legal", "all", title_field="title"),
    "blogposts": CollectionSpec("blogposts", "marketing", "all", title_field="title"),
    "testimonials": CollectionSpec("testimonials", "sales", "all", title_field="name"),
    "teammembers": CollectionSpec("teammembers", "hr", "all", title_field="name"),
    "users": CollectionSpec("users", "hr", "all", title_field="name"),
    "invoices": CollectionSpec("invoices", "finance", "finance", title_field="invoiceNumber"),
    "jobapplications": CollectionSpec("jobapplications", "hr", "hr", title_field="fullName"),
    "kpitargets": CollectionSpec("kpitargets", "operations", "operations", title_field="targetCode"),
    "leavebalances": CollectionSpec("leavebalances", "hr", "employee", title_field="fiscalYear"),
    "platformroles": CollectionSpec("platformroles", "operations", "all", title_field="name"),
    "metriclibraries": CollectionSpec("metriclibraries", "operations", "all", title_field="metricName"),
    "platformworkspaces": CollectionSpec("platformworkspaces", "general", "all", title_field="name"),
    "contracts": CollectionSpec("contracts", "sales", "employee", title_field="contractNumber"),
    "soptemplates": CollectionSpec("soptemplates", "operations", "employee", title_field="title"),
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
    "files", "chatmessages", "chatrooms", "activitylogs",
    "activitylogs_archive", "emaillogs", "emailsuppressions",
    "gst_reconcile_snapshots", "placementsnapshots", "placementsnapshotrows",
    "scoresnapshots", "accounttype_migration_snapshot_1791371360357",
    "elite_plan_migration_snapshot_1791377455724", "webhookevents",
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
