/**
 * Fakhri ERP AI — Protected Admin & Developer Settings Modal
 * Houses offline JWT management, authorized runtime role switching, and MongoDB Atlas knowledge synchronization.
 */
import React, { useState } from "react";
import {
  SettingsIcon,
  ShieldCheckIcon,
  DatabaseIcon,
  TerminalIcon,
  RefreshCwIcon,
  XIcon,
} from "../ui/Icons";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { IconButton } from "../ui/IconButton";
import type { PersonaInfo, RoleType, HealthTelemetry } from "./WorkspaceShell";

export interface AdminSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  activeRole: RoleType | "none";
  tokens: Record<RoleType, string>;
  pastedInput: string;
  setPastedInput: (val: string) => void;
  onAssignRole: (role: RoleType) => void;
  onAutoAssign: () => void;
  onSelectRole: (role: RoleType) => void;
  detectedRole: { role?: string; sub?: string; exp?: number } | null;
  activeTokenClaims?: { role?: string; sub?: string; exp?: number } | null;
  healthInfo?: HealthTelemetry;
  isSyncing: boolean;
  syncStatus: string | null;
  lastSyncTime: string | null;
  lastSyncChunks: number | null;
  onSyncMongo: () => Promise<void>;
  roleLabels: Record<RoleType, PersonaInfo>;
  enableDevAuth?: boolean;
}

export const AdminSettingsModal: React.FC<AdminSettingsModalProps> = ({
  isOpen,
  onClose,
  activeRole,
  tokens,
  pastedInput,
  setPastedInput,
  onAssignRole,
  onAutoAssign,
  onSelectRole,
  detectedRole,
  activeTokenClaims,
  healthInfo,
  isSyncing,
  syncStatus,
  lastSyncTime,
  lastSyncChunks,
  onSyncMongo,
  roleLabels,
  enableDevAuth = false,
}) => {
  const [activeTab, setActiveTab] = useState<"roles" | "jwt" | "sync">(
    enableDevAuth ? "roles" : "sync"
  );
  const effectiveTab = enableDevAuth ? activeTab : "sync";
  const [feedbackMsg, setFeedbackMsg] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleRoleClick = (role: RoleType) => {
    const hasToken = Boolean(tokens[role]);
    if (!hasToken) {
      setFeedbackMsg(`No JWT token loaded for ${roleLabels[role].name}. Paste a token under the "JWT Credentials" tab first.`);
      setTimeout(() => setFeedbackMsg(null), 4000);
      return;
    }
    onSelectRole(role);
    setFeedbackMsg(`Successfully switched runtime context to ${roleLabels[role].name} (${role.toUpperCase()}).`);
    setTimeout(() => setFeedbackMsg(null), 3000);
  };

  return (
    <div className="fkh-modal-backdrop" onClick={onClose}>
      <div
        className="fkh-modal-panel fkh-admin-settings-panel"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="fkh-admin-settings-title"
      >
        {/* Modal Header */}
        <div className="fkh-modal-header">
          <div className="fkh-modal-title-wrap">
            <TerminalIcon size={18} />
            <h3 id="fkh-admin-settings-title" className="fkh-modal-title">
              {enableDevAuth ? "Admin & Developer Settings" : "Admin Workspace Settings"}
            </h3>
          </div>
          <IconButton
            icon={<XIcon size={16} />}
            title="Close Settings"
            aria-label="Close Settings"
            onClick={onClose}
            size="sm"
          />
        </div>

        {/* Modal Tabs */}
        <div className="fkh-settings-tab-bar" role="tablist" aria-label="Admin settings categories">
          {enableDevAuth && (
            <>
              <button
                type="button"
                role="tab"
                id="fkh-tab-roles"
                aria-selected={effectiveTab === "roles"}
                aria-controls="fkh-tabpanel-roles"
                className={`fkh-settings-tab-btn ${effectiveTab === "roles" ? "active" : ""}`}
                onClick={() => setActiveTab("roles")}
              >
                <ShieldCheckIcon size={15} />
                <span>Authorized Roles</span>
              </button>
              <button
                type="button"
                role="tab"
                id="fkh-tab-jwt"
                aria-selected={effectiveTab === "jwt"}
                aria-controls="fkh-tabpanel-jwt"
                className={`fkh-settings-tab-btn ${effectiveTab === "jwt" ? "active" : ""}`}
                onClick={() => setActiveTab("jwt")}
              >
                <SettingsIcon size={15} />
                <span>JWT Credentials</span>
              </button>
            </>
          )}
          <button
            type="button"
            role="tab"
            id="fkh-tab-sync"
            aria-selected={effectiveTab === "sync"}
            aria-controls="fkh-tabpanel-sync"
            className={`fkh-settings-tab-btn ${effectiveTab === "sync" ? "active" : ""}`}
            onClick={() => setActiveTab("sync")}
          >
            <DatabaseIcon size={15} />
            <span>Knowledge Base Sync</span>
          </button>
        </div>

        {/* Tab 1: Authorized Roles Switcher */}
        {enableDevAuth && effectiveTab === "roles" && (
          <div
            className="fkh-settings-tab-body"
            role="tabpanel"
            id="fkh-tabpanel-roles"
            aria-labelledby="fkh-tab-roles"
          >
            <div className="fkh-settings-section-header">
              <span className="fkh-section-heading">Runtime Persona Switcher</span>
              <p className="fkh-section-subtext">
                Switching roles immediately adapts navigation, suggestions, and enforces pre-retrieval vector boundaries.
              </p>
            </div>

            {feedbackMsg && (
              <div className="fkh-settings-alert-banner">
                <span>{feedbackMsg}</span>
              </div>
            )}

            <div className="fkh-settings-roles-grid">
              {(Object.keys(roleLabels) as RoleType[]).map((r) => {
                const info = roleLabels[r];
                const isSelected = activeRole === r;
                const hasToken = Boolean(tokens[r]);

                return (
                  <button
                    key={r}
                    type="button"
                    aria-pressed={isSelected}
                    className={`fkh-settings-role-card ${isSelected ? "selected" : ""} ${
                      !hasToken ? "disabled" : ""
                    }`}
                    onClick={() => handleRoleClick(r)}
                  >
                    <div className="fkh-role-card-top">
                      <div className="fkh-role-card-title-row">
                        <span className="fkh-role-dot" style={{ backgroundColor: info.color }} />
                        <span className="fkh-role-name">{info.name}</span>
                      </div>
                      <div className="fkh-role-card-badges">
                        {isSelected && (
                          <Badge variant={info.badgeVariant} size="sm">
                            ACTIVE
                          </Badge>
                        )}
                        {hasToken ? (
                          <span className="fkh-token-status-pill token-active" title="Token Loaded">
                            ✓ Ready
                          </span>
                        ) : (
                          <span className="fkh-token-status-pill token-missing" title="No Token Loaded">
                            No Token
                          </span>
                        )}
                      </div>
                    </div>

                    <span className="fkh-role-designation">{info.title}</span>
                    <p className="fkh-role-desc">{info.desc}</p>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Tab 2: JWT Management & Assignment */}
        {enableDevAuth && effectiveTab === "jwt" && (
          <div
            className="fkh-settings-tab-body"
            role="tabpanel"
            id="fkh-tabpanel-jwt"
            aria-labelledby="fkh-tab-jwt"
          >
            <div className="fkh-settings-section-header">
              <span className="fkh-section-heading">Offline Token Assignment</span>
              <p className="fkh-section-subtext">
                Paste offline-generated development tokens to populate roles. Tokens remain in-memory and are never leaked.
              </p>
            </div>

            <div className="fkh-jwt-input-box">
              <label htmlFor="fkh-admin-jwt" className="fkh-form-label">
                Paste Raw JWT Token:
              </label>
              <textarea
                id="fkh-admin-jwt"
                className="fkh-jwt-textarea"
                rows={3}
                placeholder="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhdHVsLmJhbmdyZSIsImNv..."
                value={pastedInput}
                onChange={(e) => setPastedInput(e.target.value)}
              />

              {detectedRole ? (
                <div
                  className={`fkh-detected-callout ${
                    typeof detectedRole.exp === "number" && detectedRole.exp * 1000 < Date.now()
                      ? "fkh-callout-warn"
                      : ""
                  }`}
                >
                  <div className="fkh-detected-meta">
                    <span className="fkh-callout-text">
                      Detected Payload: <strong>{detectedRole.role}</strong> ({detectedRole.sub})
                    </span>
                    {detectedRole.exp && (
                      <span className="fkh-exp-text">
                        {detectedRole.exp * 1000 < Date.now() ? "EXPIRED: " : "Expires: "}
                        {new Date(detectedRole.exp * 1000).toLocaleString()}
                      </span>
                    )}
                  </div>
                  <Button variant="primary" size="sm" onClick={onAutoAssign}>
                    Assign to {detectedRole.role}
                  </Button>
                </div>
              ) : (
                pastedInput.trim() && (
                  <div className="fkh-detected-callout fkh-callout-warn">
                    <span>Invalid JWT structure. Please paste a valid token with header, payload, and signature.</span>
                  </div>
                )
              )}
            </div>

            <div className="fkh-manual-assign-wrap">
              <span className="fkh-form-label">Manual Role Mapping:</span>
              <div className="fkh-manual-assign-buttons">
                {(Object.keys(roleLabels) as RoleType[]).map((r) => (
                  <button
                    key={r}
                    type="button"
                    className="fkh-manual-assign-chip"
                    onClick={() => onAssignRole(r)}
                  >
                    Assign to {r}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Tab 3: Knowledge Base Synchronization & Telemetry */}
        {effectiveTab === "sync" && (
          <div
            className="fkh-settings-tab-body"
            role="tabpanel"
            id="fkh-tabpanel-sync"
            aria-labelledby="fkh-tab-sync"
          >
            <div className="fkh-settings-section-header">
              <span className="fkh-section-heading">MongoDB Atlas Knowledge Synchronization</span>
              <p className="fkh-section-subtext">
                Trigger incremental and vector synchronization from live MongoDB Atlas collections directly into ChromaDB.
              </p>
            </div>

            <div className="fkh-kb-status-card">
              <div className="fkh-kb-status-row">
                <span className="fkh-kb-key">Backend API Status:</span>
                <span
                  className={`fkh-kb-val ${
                    healthInfo?.status === "online" ? "fkh-text-success" : "fkh-callout-warn"
                  }`}
                >
                  {healthInfo?.status === "online"
                    ? `Online (${healthInfo.llm_provider || "active"} / ${healthInfo.orchestrator || "langgraph"})`
                    : healthInfo?.status === "offline"
                    ? "Offline / Unreachable"
                    : "Checking..."}
                </span>
              </div>
              <div className="fkh-kb-status-row">
                <span className="fkh-kb-key">Last Sync Delta:</span>
                <span className="fkh-kb-val">
                  {lastSyncChunks !== null && lastSyncChunks !== undefined
                    ? `${lastSyncChunks} chunks upserted`
                    : "Run on-demand sync to verify live status"}
                </span>
              </div>
              <div className="fkh-kb-status-row">
                <span className="fkh-kb-key">Last Synchronization:</span>
                <span className="fkh-kb-val">
                  {lastSyncTime ? new Date(lastSyncTime).toLocaleString() : "Not triggered in current session"}
                </span>
              </div>
              <div className="fkh-kb-status-row">
                <span className="fkh-kb-key">Vector Embedding Model:</span>
                <span className="fkh-kb-val">Multilingual-E5-Small (384-dim)</span>
              </div>
              {activeTokenClaims?.exp && activeTokenClaims.exp * 1000 < Date.now() && (
                <div className="fkh-kb-status-row">
                  <span className="fkh-kb-key">Token Expiry Alert:</span>
                  <span className="fkh-kb-val" style={{ color: "var(--fkh-danger)" }}>
                    Active JWT expired — refresh token before syncing
                  </span>
                </div>
              )}
            </div>

            {activeRole !== "super-admin" && activeRole !== "founder" && (
              <div className="fkh-settings-alert-banner">
                <span>
                  Note: Backend enforces Admin/Founder-only access on POST /sync/mongo. Non-admin roles will receive HTTP 403 Forbidden.
                </span>
              </div>
            )}

            <div className="fkh-sync-action-box">
              <Button
                variant="primary"
                size="md"
                onClick={onSyncMongo}
                disabled={isSyncing}
              >
                <RefreshCwIcon size={14} className={isSyncing ? "fkh-spin" : ""} />
                <span>{isSyncing ? "Synchronizing MongoDB Atlas..." : "Sync MongoDB Collections"}</span>
              </Button>
              {syncStatus && <p className="fkh-sync-feedback">{syncStatus}</p>}
            </div>
          </div>
        )}

        <div className="fkh-modal-footer">
          <Button variant="secondary" size="md" onClick={onClose} type="button">
            Close
          </Button>
        </div>
      </div>
    </div>
  );
};
