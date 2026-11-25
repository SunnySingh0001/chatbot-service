/**
 * Fakhri ERP AI — User Profile & Identity Modal
 * Displays authenticated user persona, role badge, organization workspace, and sign-out controls.
 * Does not expose raw JWT tokens to protect credentials.
 */
import React from "react";
import { UserIcon, ShieldCheckIcon, SettingsIcon, XIcon } from "../ui/Icons";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { IconButton } from "../ui/IconButton";
import type { PersonaInfo, RoleType } from "./WorkspaceShell";

export interface UserProfileModalProps {
  isOpen: boolean;
  onClose: () => void;
  activeRole: RoleType | "none";
  activePersona: PersonaInfo | null;
  userSub?: string;
  onOpenSettings: () => void;
  onSignOut: () => void;
  enableDevAuth?: boolean;
}

export const UserProfileModal: React.FC<UserProfileModalProps> = ({
  isOpen,
  onClose,
  activeRole,
  activePersona,
  userSub,
  onOpenSettings,
  onSignOut,
  enableDevAuth = false,
}) => {
  if (!isOpen) return null;

  const displayName = activePersona ? activePersona.name : "Unauthenticated Guest";
  const displayEmail = userSub
    ? userSub
    : activePersona
    ? `${activePersona.name.toLowerCase().replace(/[^a-z0-9]/g, ".")}@fakhri.internal`
    : "guest@fakhri.internal";

  const getInitials = (name: string): string => {
    const parts = name.split(" ").filter(Boolean);
    if (parts.length >= 2) {
      return (parts[0][0] + parts[1][0]).toUpperCase();
    }
    return name.slice(0, 2).toUpperCase();
  };

  return (
    <div className="fkh-modal-backdrop" onClick={onClose}>
      <div
        className="fkh-modal-panel fkh-profile-modal-panel"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="fkh-profile-title"
      >
        {/* Modal Header */}
        <div className="fkh-modal-header">
          <div className="fkh-modal-title-wrap">
            <UserIcon size={18} />
            <h3 id="fkh-profile-title" className="fkh-modal-title">User Profile & Access</h3>
          </div>
          <IconButton
            icon={<XIcon size={16} />}
            title="Close Profile"
            aria-label="Close Profile"
            onClick={onClose}
            size="sm"
          />
        </div>

        {/* Profile Card Summary */}
        <div className="fkh-profile-hero-card">
          <div
            className="fkh-profile-avatar-large"
            style={{ backgroundColor: activePersona ? activePersona.color : "#64748b" }}
          >
            {activePersona ? getInitials(activePersona.name) : <UserIcon size={24} />}
          </div>
          <div className="fkh-profile-hero-details">
            <h4 className="fkh-profile-name">{displayName}</h4>
            <span className="fkh-profile-email">{displayEmail}</span>
            <div className="fkh-profile-badge-row">
              {activePersona ? (
                <Badge variant={activePersona.badgeVariant} size="sm">
                  {activeRole.toUpperCase()}
                </Badge>
              ) : (
                <Badge variant="slate" size="sm">
                  GUEST
                </Badge>
              )}
              <span className="fkh-profile-org-label">Fakhri IT Services</span>
            </div>
          </div>
        </div>

        {/* Access & Scope Section */}
        <div className="fkh-profile-section">
          <span className="fkh-section-heading">Organizational Role & Scope</span>
          <div className="fkh-profile-scope-box">
            <div className="fkh-scope-row">
              <span className="fkh-scope-key">Designation:</span>
              <span className="fkh-scope-val">
                {activePersona ? activePersona.title : "Unassigned"}
              </span>
            </div>
            <div className="fkh-scope-row">
              <span className="fkh-scope-key">Security Clearance:</span>
              <span className="fkh-scope-val">
                {activeRole === "super-admin" || activeRole === "founder"
                  ? "Level 5 — Platform Root / All Collections"
                  : activeRole === "hr"
                  ? "Level 3 — HR & Employee Directories"
                  : activeRole === "operations"
                  ? "Level 3 — Operations & Tasks"
                  : activeRole === "finance"
                  ? "Level 3 — Invoices & PSA Billing"
                  : activeRole === "it"
                  ? "Level 3 — IT Assets & Infrastructure"
                  : activeRole === "employee"
                  ? "Level 1 — General Policies & Self Directory"
                  : "Level 0 — Public Handbook Only"}
              </span>
            </div>
            <div className="fkh-scope-row">
              <span className="fkh-scope-key">Scope Description:</span>
              <span className="fkh-scope-val">
                {activePersona ? activePersona.desc : "Read-only access to unrestricted public documents."}
              </span>
            </div>
          </div>
        </div>

        {/* Security Notice */}
        <div className="fkh-profile-security-callout">
          <ShieldCheckIcon size={16} className="fkh-security-icon" />
          <span>
            Role-Based Access Control is enforced server-side inside MongoDB and ChromaDB vector queries.
          </span>
        </div>

        {/* Footer Actions */}
        <div className="fkh-modal-footer fkh-profile-modal-footer">
          {(enableDevAuth || activeRole === "super-admin" || activeRole === "founder") && (
            <Button
              variant="secondary"
              size="md"
              onClick={() => {
                onClose();
                onOpenSettings();
              }}
            >
              <SettingsIcon size={14} />
              <span>{enableDevAuth ? "Settings & Roles" : "Knowledge Base Sync"}</span>
            </Button>
          )}

          {activeRole !== "none" ? (
            <Button
              variant="danger"
              size="md"
              onClick={() => {
                onSignOut();
                onClose();
              }}
            >
              Sign Out
            </Button>
          ) : enableDevAuth ? (
            <Button
              variant="primary"
              size="md"
              onClick={() => {
                onClose();
                onOpenSettings();
              }}
            >
              Sign In (Load Role)
            </Button>
          ) : (
            <span className="fkh-host-auth-note" style={{ fontSize: "12px", color: "var(--fkh-text-secondary)" }}>
              Sign in via host ERP portal
            </span>
          )}
        </div>
      </div>
    </div>
  );
};
