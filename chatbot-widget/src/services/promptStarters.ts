/**
 * Role-aware starter prompt definitions for the welcome experience.
 */

export interface StarterPromptItem {
  id: string;
  category: string;
  title: string;
  prompt: string;
  icon: string;
  allowedRoles: string[]; // "*" means accessible to all roles
}

export const STARTER_PROMPTS: StarterPromptItem[] = [
  {
    id: "hr-hierarchy",
    category: "HR & TEAM HIERARCHY",
    title: "Organization Hierarchy",
    prompt: "Who reports to whom in our organization?",
    icon: "👥",
    allowedRoles: ["*"],
  },
  {
    id: "operations-approvals",
    category: "OPERATIONS & APPROVALS",
    title: "Operational Status",
    prompt: "Show the status of pending operational approvals.",
    icon: "📋",
    allowedRoles: ["super-admin", "founder", "operations", "hr"],
  },
  {
    id: "psa-clients",
    category: "PSA & CLIENT DIRECTORY",
    title: "Client Directory",
    prompt: "Find information about our active client companies.",
    icon: "💼",
    allowedRoles: ["super-admin", "founder", "operations", "finance"],
  },
  {
    id: "finance-invoices",
    category: "FINANCE & INVOICES",
    title: "Billing & Invoices",
    prompt: "Summarize pending invoices and billing information.",
    icon: "💰",
    allowedRoles: ["super-admin", "founder", "finance"],
  },
  {
    id: "company-policies",
    category: "COMPANY POLICIES",
    title: "Internal Policies",
    prompt: "Find our internal company policies.",
    icon: "📖",
    allowedRoles: ["*"],
  },
  {
    id: "it-support",
    category: "IT & TECHNICAL SUPPORT",
    title: "IT Procedures",
    prompt: "What are our internal IT infrastructure and security procedures?",
    icon: "💻",
    allowedRoles: ["*"],
  },
];

/**
 * Filter starter prompts based on user's active role.
 */
export function getVisiblePrompts(activeRole: string = "none"): StarterPromptItem[] {
  return STARTER_PROMPTS.filter((item) => {
    if (item.allowedRoles.includes("*")) return true;
    if (activeRole === "super-admin" || activeRole === "founder") return true;
    return item.allowedRoles.includes(activeRole);
  });
}

/**
 * Returns the list of authorized modules accessible by a given role.
 */
export function getRoleAccessibleModules(role: string = "none"): string[] {
  switch (role) {
    case "super-admin":
      return ["All Modules", "HR", "Operations", "Finance", "PSA Clients", "IT Infrastructure", "System Telemetry"];
    case "founder":
      return ["All Modules", "HR", "Operations", "Finance", "PSA Clients", "Company Policies"];
    case "hr":
      return ["HR & Team Hierarchy", "Employee Directory", "Compensation & Salary Bands", "Company Policies"];
    case "operations":
      return ["Operations Workflows", "Pending Approvals", "PSA Clients", "FBA Prep", "Employee Directory"];
    case "finance":
      return ["Invoices & Billing", "PSA Client Directory", "Pricing Plans", "Company Policies"];
    case "it":
      return ["IT Assets & Infrastructure", "Security Guidelines", "Technical Procedures", "Company Policies"];
    case "employee":
      return ["General Company Policies", "Leave Rules", "Public Employee Directory", "FAQs"];
    default:
      return ["Public Handbook", "General Policies"];
  }
}

export interface WelcomeCopy {
  brandTag: string;
  heading: string;
  subtitle: string;
}

const ROLE_WELCOME_CONTEXT: Record<string, { roleLabel: string; scopeSummary: string }> = {
  "super-admin": {
    roleLabel: "Super Admin",
    scopeSummary: "ERP governance, teams, operational approvals, financials, and IT systems",
  },
  founder: {
    roleLabel: "Founder",
    scopeSummary: "executive insights, operations, PSA clients, finance, and company policies",
  },
  hr: {
    roleLabel: "HR",
    scopeSummary: "org hierarchy, employee directory, compensation bands, and HR policies",
  },
  operations: {
    roleLabel: "Operations",
    scopeSummary: "operational approvals, PSA client workflows, and team coordination",
  },
  finance: {
    roleLabel: "Finance & Accounts",
    scopeSummary: "invoices, billing summaries, PSA client records, and financial policies",
  },
  it: {
    roleLabel: "IT",
    scopeSummary: "IT infrastructure, security guidelines, assets, and technical procedures",
  },
  employee: {
    roleLabel: "Employee",
    scopeSummary: "company policies, leave guidelines, team directory, and IT support",
  },
};

/** Returns brand-aligned welcome badge label, heading, and subtitle personalized for active role and user. */
export function getPersonalizedWelcomeCopy(
  activeRole: string = "none",
  activePersonaName?: string
): WelcomeCopy {
  const roleMeta = ROLE_WELCOME_CONTEXT[activeRole];
  const firstName = activePersonaName?.trim().split(/\s+/)[0] || "";

  return {
    brandTag: roleMeta ? `Fakhri AI · ${roleMeta.roleLabel}` : "Fakhri AI",
    heading: firstName ? `Hi ${firstName}, how can I help you today?` : "How can I help you today?",
    subtitle: roleMeta
      ? `Your Fakhri AI assistant for ${roleMeta.scopeSummary}.`
      : "Your intelligent Fakhri AI assistant for company knowledge, policies, and operations.",
  };
}
