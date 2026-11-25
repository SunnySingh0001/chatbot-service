/**
 * Fakhri ERP AI — Collapsible Contextual Panel (Area C)
 * Displays verified enterprise session scope, MongoDB knowledge catalog, live citations, and system telemetry.
 */
import React, { useState } from "react";
import {
  XIcon,
  DatabaseIcon,
  ShieldCheckIcon,
  FakhriLogoIcon,
} from "../ui/Icons";
import { Badge } from "../ui/Badge";
import { IconButton } from "../ui/IconButton";
import type { PersonaInfo, RoleType, HealthTelemetry } from "./WorkspaceShell";
import type { StoredConversation } from "../../services/sessionStore";

export interface ContextualPanelProps {
  isOpen: boolean;
  onClose: () => void;
  activeRole: RoleType | "none";
  activePersona: PersonaInfo | null;
  activeToken: string;
  activeConversation?: StoredConversation;
  lastSyncTime?: string | null;
  lastSyncChunks?: number | null;
  healthInfo?: HealthTelemetry;
  activeTokenClaims?: { role?: string; sub?: string; exp?: number } | null;
  isTemporary?: boolean;
}

export const ContextualPanel: React.FC<ContextualPanelProps> = ({
  isOpen,
  onClose,
  activeRole,
  activePersona,
  activeToken,
  activeConversation,
  lastSyncTime,
  lastSyncChunks,
  healthInfo,
  activeTokenClaims,
  isTemporary = false,
}) => {
  const [activeTab, setActiveTab] = useState<"scope" | "knowledge" | "telemetry">("scope");

  if (!isOpen) return null;

  // Extract unique citations from current active conversation messages
  const activeSources: string[] = [];
  if (activeConversation?.messages) {
    for (const msg of activeConversation.messages) {
      if (msg.sources && Array.isArray(msg.sources)) {
        for (const src of msg.sources) {
          if (!activeSources.includes(src)) {
            activeSources.push(src);
          }
        }
      }
    }
  }

  return (
    <aside
      className="fkh-contextual-panel"
      role="region"
      aria-label="Workspace Context and Live Sources"
    >
      {/* Panel Header */}
      <div className="fkh-contextual-header">
        <div className="fkh-contextual-title-wrap">
          <FakhriLogoIcon size={16} />
          <h3 className="fkh-contextual-title">Workspace Context</h3>
        </div>
        <IconButton
          icon={<XIcon size={16} />}
          title="Close Contextual Panel"
          aria-label="Close Contextual Panel"
          onClick={onClose}
          size="sm"
        />
      </div>

      {/* Tabs */}
      <div className="fkh-contextual-tabs" role="tablist" aria-label="Contextual sections">
        <button
          type="button"
          role="tab"
          id="fkh-ctx-tab-scope"
          aria-selected={activeTab === "scope"}
          aria-controls="fkh-ctx-panel-scope"
          className={`fkh-contextual-tab ${activeTab === "scope" ? "active" : ""}`}
          onClick={() => setActiveTab("scope")}
        >
          <ShieldCheckIcon size={14} />
          <span>RBAC Scope</span>
        </button>
        <button
          type="button"
          role="tab"
          id="fkh-ctx-tab-knowledge"
          aria-selected={activeTab === "knowledge"}
          aria-controls="fkh-ctx-panel-knowledge"
          className={`fkh-contextual-tab ${activeTab === "knowledge" ? "active" : ""}`}
          onClick={() => setActiveTab("knowledge")}
        >
          <DatabaseIcon size={14} />
          <span>Knowledge</span>
        </button>
        <button
          type="button"
          role="tab"
          id="fkh-ctx-tab-telemetry"
          aria-selected={activeTab === "telemetry"}
          aria-controls="fkh-ctx-panel-telemetry"
          className={`fkh-contextual-tab ${activeTab === "telemetry" ? "active" : ""}`}
          onClick={() => setActiveTab("telemetry")}
        >
          <span>Telemetry</span>
        </button>
      </div>

      {/* Tab Content */}
      <div className="fkh-contextual-body">
        {/* Tab 1: RBAC Scope */}
        {activeTab === "scope" && (
          <div
            className="fkh-contextual-tab-content"
            role="tabpanel"
            id="fkh-ctx-panel-scope"
            aria-labelledby="fkh-ctx-tab-scope"
          >
            <div className="fkh-contextual-card">
              <span className="fkh-contextual-label">Active Persona:</span>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "4px" }}>
                {activePersona ? (
                  <>
                    <Badge variant={activePersona.badgeVariant} size="md">
                      {activeRole.toUpperCase()}
                    </Badge>
                    <strong style={{ fontSize: "13px", color: "var(--fkh-text-primary)" }}>
                      {activePersona.name}
                    </strong>
                  </>
                ) : (
                  <span style={{ fontSize: "12px", color: "var(--fkh-text-muted)" }}>
                    No Persona Assigned
                  </span>
                )}
              </div>
            </div>

            <div className="fkh-contextual-card">
              <span className="fkh-contextual-label">Document Access Level:</span>
              <p style={{ margin: "4px 0 0", fontSize: "12px", color: "var(--fkh-text-secondary)", lineHeight: 1.4 }}>
                {activePersona ? activePersona.desc : "Unauthenticated — public documents only."}
              </p>
            </div>

            <div className="fkh-contextual-card">
              <span className="fkh-contextual-label">Active Session:</span>
              <div style={{ marginTop: "4px", fontSize: "11.5px", color: "var(--fkh-text-secondary)" }}>
                <div>
                  Server ID:{" "}
                  <code style={{ fontFamily: "var(--fkh-font-mono)", color: "var(--fkh-text-primary)" }}>
                    {isTemporary
                      ? "Temporary Mode (Not Saved)"
                      : activeConversation?.backendSessionId
                      ? `${activeConversation.backendSessionId.slice(0, 14)}...`
                      : "Pending first query"}
                  </code>
                </div>
                <div>Turns: <strong>{activeConversation?.messages.length || 0} messages</strong></div>
              </div>
            </div>

            <div className="fkh-contextual-card">
              <span className="fkh-contextual-label">JWT Token Status:</span>
              <div style={{ marginTop: "4px", display: "flex", alignItems: "center", gap: "6px" }}>
                {(() => {
                  const isExpired =
                    typeof activeTokenClaims?.exp === "number" &&
                    activeTokenClaims.exp * 1000 < Date.now();
                  return (
                    <>
                      <span
                        style={{
                          display: "inline-block",
                          width: "8px",
                          height: "8px",
                          borderRadius: "50%",
                          backgroundColor: !activeToken
                            ? "var(--fkh-warning)"
                            : isExpired
                            ? "var(--fkh-danger)"
                            : "var(--fkh-success)",
                        }}
                      />
                      <span style={{ fontSize: "12px", fontFamily: "var(--fkh-font-mono)", color: "var(--fkh-text-primary)" }}>
                        {!activeToken
                          ? "Missing (Guest Mode)"
                          : isExpired
                          ? "Expired — Re-authenticate"
                          : "Authenticated Session"}
                      </span>
                    </>
                  );
                })()}
              </div>
            </div>

            <div className="fkh-contextual-card">
              <span className="fkh-contextual-label">Security Invariant:</span>
              <p style={{ margin: "4px 0 0", fontSize: "11.5px", color: "var(--fkh-text-secondary)" }}>
                Pre-retrieval vector where-clause filtering ensures unauthorized company documents are mathematically excluded before LLM context generation.
              </p>
            </div>
          </div>
        )}

        {/* Tab 2: Knowledge Base & Live Sources */}
        {activeTab === "knowledge" && (
          <div
            className="fkh-contextual-tab-content"
            role="tabpanel"
            id="fkh-ctx-panel-knowledge"
            aria-labelledby="fkh-ctx-tab-knowledge"
          >
            <div className="fkh-contextual-card">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span className="fkh-contextual-label">Backend & Knowledge Source:</span>
                {healthInfo?.status === "online" ? (
                  <Badge variant="green" size="sm">Online</Badge>
                ) : healthInfo?.status === "offline" ? (
                  <Badge variant="red" size="sm">Offline</Badge>
                ) : (
                  <Badge variant="slate" size="sm">Checking...</Badge>
                )}
              </div>
              <p style={{ margin: "4px 0 0", fontSize: "12.5px", fontWeight: 600, color: "var(--fkh-text-primary)" }}>
                MongoDB Atlas Catalog
              </p>
              <span style={{ fontSize: "11px", color: "var(--fkh-text-secondary)" }}>
                HR, Operations, PSA Clients, Invoices, Departments
              </span>
            </div>

            <div className="fkh-contextual-card">
              <span className="fkh-contextual-label">ChromaDB Vector Store:</span>
              <p style={{ margin: "4px 0 0", fontSize: "12.5px", fontWeight: 600, color: "var(--fkh-text-primary)" }}>
                {lastSyncChunks !== null && lastSyncChunks !== undefined
                  ? `Synced (${lastSyncChunks} chunks upserted in last run)`
                  : "Vector Store Active (run sync to verify delta)"}
              </p>
              <span style={{ fontSize: "11px", color: "var(--fkh-text-secondary)" }}>
                Multilingual-E5-Small (384 Dimensions, Asymmetric Prefixes)
              </span>
            </div>

            <div className="fkh-contextual-card">
              <span className="fkh-contextual-label">Last Knowledge Sync:</span>
              <p style={{ margin: "4px 0 0", fontSize: "12px", color: "var(--fkh-text-secondary)" }}>
                {lastSyncTime ? new Date(lastSyncTime).toLocaleString() : "Not triggered in current browser session"}
              </p>
            </div>

            {/* Live Citations from Conversation */}
            <div className="fkh-contextual-card">
              <span className="fkh-contextual-label">Cited Sources in Active Thread:</span>
              {activeSources.length === 0 ? (
                <p style={{ margin: "4px 0 0", fontSize: "11.5px", color: "var(--fkh-text-muted)", fontStyle: "italic" }}>
                  No citations cited in active thread yet. Ask a question to view ground-truth documents.
                </p>
              ) : (
                <div style={{ display: "flex", flexWrap: "wrap", gap: "4px", marginTop: "6px" }}>
                  {activeSources.map((src, idx) => (
                    <span key={idx} className="chat-source-chip">
                      📄 {src.replace(/^mongo:/, "")}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Tab 3: System Telemetry */}
        {activeTab === "telemetry" && (
          <div
            className="fkh-contextual-tab-content"
            role="tabpanel"
            id="fkh-ctx-panel-telemetry"
            aria-labelledby="fkh-ctx-tab-telemetry"
          >
            <div className="fkh-contextual-card">
              <span className="fkh-contextual-label">Active LLM Provider:</span>
              <p style={{ margin: "4px 0 0", fontSize: "12.5px", fontWeight: 600, color: "var(--fkh-text-primary)" }}>
                {healthInfo?.llm_provider ? healthInfo.llm_provider.toUpperCase() : "Unavailable"}
              </p>
              <span style={{ fontSize: "11px", color: "var(--fkh-text-secondary)" }}>
                Reported dynamically from GET /health
              </span>
            </div>

            <div className="fkh-contextual-card">
              <span className="fkh-contextual-label">Orchestrator Engine:</span>
              <p style={{ margin: "4px 0 0", fontSize: "12.5px", fontWeight: 600, color: "var(--fkh-text-primary)" }}>
                {healthInfo?.orchestrator ? healthInfo.orchestrator.toUpperCase() : "LangGraph"} State Machine
              </p>
              <span style={{ fontSize: "11px", color: "var(--fkh-text-secondary)" }}>
                Stateless In-Memory DAG (checkpointer=None)
              </span>
            </div>

            <div className="fkh-contextual-card">
              <span className="fkh-contextual-label">Streaming Delivery:</span>
              <p style={{ margin: "4px 0 0", fontSize: "12.5px", fontWeight: 600, color: "var(--fkh-text-primary)" }}>
                Server-Sent Events (SSE)
              </p>
              <span style={{ fontSize: "11px", color: "var(--fkh-text-secondary)" }}>
                Unidirectional HTTP/1.1 chunked transfer stream
              </span>
            </div>
          </div>
        )}
      </div>
    </aside>
  );
};
