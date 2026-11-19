/**
 * Demo host page providing in-memory token switching across verified enterprise roles and mounting ChatWidget.
 */
import React, { useState } from "react";
import { ChatWidget } from "../components/ChatWidget";
import "../styles/widget.css";
import "./demo.css";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export type RoleType = "super-admin" | "founder" | "hr" | "operations" | "it" | "finance" | "employee";

interface DecodedToken {
  role?: string;
  sub?: string;
  exp?: number;
}

const parseJwt = (rawToken: string): DecodedToken | null => {
  try {
    const parts = rawToken.trim().split(".");
    if (parts.length === 3) {
      const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
      const jsonPayload = decodeURIComponent(
        atob(base64)
          .split("")
          .map((c) => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2))
          .join("")
      );
      return JSON.parse(jsonPayload) as DecodedToken;
    }
  } catch {
    return null;
  }
  return null;
};

export const DemoApp: React.FC = () => {
  const [pastedInput, setPastedInput] = useState("");
  const [tokens, setTokens] = useState<Record<RoleType, string>>({
    "super-admin": "",
    founder: "",
    hr: "",
    operations: "",
    it: "",
    finance: "",
    employee: "",
  });
  const [activeRole, setActiveRole] = useState<RoleType | "none">("none");
  const [isSyncing, setIsSyncing] = useState(false);
  const [syncStatus, setSyncStatus] = useState<string | null>(null);

  const detected = pastedInput.trim() ? parseJwt(pastedInput.trim()) : null;

  // In-memory token lookup function for the widget
  const getActiveToken = (): string => {
    if (activeRole !== "none") {
      return tokens[activeRole] || "";
    }
    return "";
  };

  const handleAssignRole = (role: RoleType) => {
    const trimmed = pastedInput.trim();
    if (trimmed) {
      setTokens((prev) => ({ ...prev, [role]: trimmed }));
      setActiveRole(role);
      setPastedInput("");
    }
  };

  const handleSyncMongo = async () => {
    if (!activeToken) return;
    setIsSyncing(true);
    setSyncStatus("Syncing MongoDB Atlas data to ChromaDB vector store...");
    try {
      const res = await fetch(`${API_URL}/sync/mongo`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${activeToken}`,
        },
      });
      const data = await res.json();
      if (res.ok) {
        setSyncStatus(`✓ Success: ${data.message} (${data.total_chunks} chunks indexed)`);
      } else {
        setSyncStatus(`✗ Sync failed: ${data.detail || "Unauthorized or error"}`);
      }
    } catch (err: unknown) {
      setSyncStatus(`✗ Sync error: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setIsSyncing(false);
    }
  };

  const handleAutoAssign = () => {
    if (detected?.role && detected.role in tokens) {
      handleAssignRole(detected.role as RoleType);
    }
  };

  const activeToken = getActiveToken();

  const roleLabels: Record<RoleType, { title: string; color: string; desc: string }> = {
    "super-admin": {
      title: "⚡ Super Admin (Atul Bangre)",
      color: "#b91c1c",
      desc: "Platform Owner / Full unrestricted company-wide access to all knowledge and data.",
    },
    founder: {
      title: "👑 Founder (Abdul Quadir)",
      color: "#dc2626",
      desc: "Full Root Access — reads all company knowledge, HR benefits, and operational data.",
    },
    hr: {
      title: "👥 HR (Disha Damwani)",
      color: "#7c3aed",
      desc: "Employee docs + Confidential compensation, salary bands L1-L6, promotion rules.",
    },
    operations: {
      title: "⚙️ Operations (Zainab Husain)",
      color: "#0891b2",
      desc: "Employee docs + Operations workflows, FBA fulfillment, department structures.",
    },
    employee: {
      title: "👤 Employee (Vivek / Aashi)",
      color: "#0284c7",
      desc: "General public policies, FAQs, service catalogs, job openings, leave guidelines.",
    },
    it: {
      title: "💻 IT (Ali Asgar)",
      color: "#059669",
      desc: "General docs + IT infrastructure, security guidelines, system administration.",
    },
    finance: {
      title: "💰 Finance & Accounts",
      color: "#d97706",
      desc: "General docs + Commercial billing, invoice records, pricing plans.",
    },
  };

  return (
    <div className="demo-container">
      <header className="demo-header">
        <h1>Fakhri IT Services — Enterprise Chatbot Testbed</h1>
        <p className="demo-subtitle">
          Manual test ground with runtime role switching across real company team members.
        </p>
      </header>

      <main className="demo-main">
        <section className="demo-card">
          <h2>Authentication Setup (Multi-Role In-Memory)</h2>
          <p className="demo-desc">
            Generate development JWTs using: <code>python -m scripts.generate_tokens</code>.
            Paste any role token below to test access boundaries. Tokens are kept strictly in memory.
          </p>

          <div className="demo-input-group">
            <textarea
              className="demo-token-input"
              value={pastedInput}
              onChange={(e) => setPastedInput(e.target.value)}
              placeholder="Paste raw JWT token here..."
              rows={3}
            />

            {detected && detected.role && (
              <div className="demo-detected-banner">
                <span>
                  Detected Payload: <strong>Role: {detected.role.toUpperCase()}</strong> (User: {detected.sub || "user"})
                </span>
                {detected.role in tokens && (
                  <button
                    type="button"
                    className="demo-btn demo-btn-primary"
                    onClick={handleAutoAssign}
                  >
                    Auto-Assign to {detected.role.toUpperCase()} Slot
                  </button>
                )}
              </div>
            )}

            <div className="demo-save-actions">
              {(Object.keys(roleLabels) as RoleType[]).map((role) => (
                <button
                  key={role}
                  type="button"
                  className="demo-btn demo-btn-secondary"
                  onClick={() => handleAssignRole(role)}
                  disabled={!pastedInput.trim()}
                >
                  Assign to {roleLabels[role].title}
                </button>
              ))}
            </div>
          </div>

          <div className="demo-switcher-group">
            <span className="demo-switcher-label">Active Role Switcher:</span>
            <div className="demo-switcher-buttons">
              {(Object.keys(roleLabels) as RoleType[]).map((role) => {
                const hasToken = Boolean(tokens[role]);
                const isActive = activeRole === role;
                return (
                  <button
                    key={role}
                    type="button"
                    className={`demo-role-btn ${isActive ? "active" : ""}`}
                    onClick={() => setActiveRole(role)}
                    disabled={!hasToken}
                    title={roleLabels[role].desc}
                  >
                    {roleLabels[role].title} {hasToken ? "✓" : "(empty)"}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="demo-status-pill">
            <span>Status:</span>
            <strong>
              {activeToken
                ? `Active Session: ${activeRole.toUpperCase()} (${activeToken.slice(0, 14)}...)`
                : "No token active (Chat calls will prompt for authentication)"}
            </strong>
          </div>

          {["super-admin", "founder"].includes(activeRole) && (
            <div style={{ marginTop: "1rem", paddingTop: "0.75rem", borderTop: "1px solid #e2e8f0" }}>
              <button
                type="button"
                className="demo-btn demo-btn-primary"
                onClick={handleSyncMongo}
                disabled={isSyncing}
                style={{ backgroundColor: "#0284c7", borderColor: "#0284c7" }}
              >
                {isSyncing ? "⏳ Syncing Knowledge Base..." : "🔄 Sync MongoDB Atlas to Knowledge Base"}
              </button>
              {syncStatus && (
                <p style={{ marginTop: "0.5rem", fontSize: "0.875rem", color: syncStatus.startsWith("✓") ? "#16a34a" : "#dc2626" }}>
                  {syncStatus}
                </p>
              )}
            </div>
          )}
        </section>

        <section className="demo-card">
          <h2>Team Member Access Boundary Matrix</h2>
          <ul className="demo-info-list">
            {(Object.keys(roleLabels) as RoleType[]).map((role) => (
              <li key={role}>
                <strong style={{ color: roleLabels[role].color }}>{roleLabels[role].title}:</strong>{" "}
                {roleLabels[role].desc}
              </li>
            ))}
          </ul>
        </section>
      </main>

      {/* Embedded Chat Widget mounted with runtime token getter */}
      <ChatWidget apiUrl={API_URL} getToken={getActiveToken} />
    </div>
  );
};
