/**
 * Fakhri ERP AI — Enterprise Workspace Demo Application
 * Integrates WorkspaceShell with in-memory JWT management and MongoDB knowledge sync.
 */
import React, { useState, useEffect, useCallback } from "react";
import { WorkspaceShell, RoleType, PersonaInfo, HealthTelemetry } from "../components/workspace/WorkspaceShell";
import { resolveDevAuthEnabled } from "../services/securityGate";
import "../styles/widget.css";
import "../styles/workspace.css";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

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
  const [lastSyncTime, setLastSyncTime] = useState<string | null>(null);
  const [lastSyncChunks, setLastSyncChunks] = useState<number | null>(null);
  const [healthInfo, setHealthInfo] = useState<HealthTelemetry>({ status: "checking" });

  const refreshHealth = useCallback(async () => {
    try {
      const cleanBaseUrl = API_URL.replace(/\/+$/, "");
      const res = await fetch(`${cleanBaseUrl}/health`);
      if (!res.ok) {
        setHealthInfo({ status: "offline", checkedAt: new Date().toISOString() });
        return;
      }
      const data = await res.json();
      setHealthInfo({
        status: data?.status === "ok" ? "online" : "offline",
        orchestrator: data?.orchestrator,
        llm_provider: data?.llm_provider,
        version: data?.version,
        checkedAt: new Date().toISOString(),
      });
    } catch {
      setHealthInfo({ status: "offline", checkedAt: new Date().toISOString() });
    }
  }, []);

  useEffect(() => {
    void refreshHealth();
  }, [refreshHealth]);

  const detected = pastedInput.trim() ? parseJwt(pastedInput.trim()) : null;
  const activeTokenClaims =
    activeRole !== "none" && tokens[activeRole] ? parseJwt(tokens[activeRole]) : null;

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
    const token = getActiveToken();
    if (!token) {
      setSyncStatus("✗ Cannot sync: Assign an active JWT persona first.");
      return;
    }
    setIsSyncing(true);
    setSyncStatus("Syncing MongoDB Atlas data to ChromaDB vector store...");
    try {
      const cleanBaseUrl = API_URL.replace(/\/+$/, "");
      const res = await fetch(`${cleanBaseUrl}/sync/mongo`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });
      const contentType = res.headers.get("content-type") || "";
      const data = contentType.includes("application/json")
        ? await res.json()
        : { detail: await res.text() };
      if (res.ok) {
        setSyncStatus(`✓ Success: ${data.message}`);
        setLastSyncTime(new Date().toISOString());
        setLastSyncChunks(typeof data.total_chunks === "number" ? data.total_chunks : null);
      } else {
        setSyncStatus(`✗ Sync failed (${res.status}): ${data.detail || "Unauthorized or error"}`);
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

  const handleSignOut = () => {
    setActiveRole("none");
  };

  const roleLabels: Record<RoleType, PersonaInfo> = {
    "super-admin": {
      title: "Super Admin (Atul Bangre)",
      name: "Atul Bangre",
      color: "#b91c1c",
      desc: "Platform Owner / Full unrestricted company-wide access to all knowledge and data.",
      badgeVariant: "red",
    },
    founder: {
      title: "Founder (Abdul Quadir)",
      name: "Abdul Quadir",
      color: "#dc2626",
      desc: "Full Root Access — reads all company knowledge, HR benefits, and operational data.",
      badgeVariant: "red",
    },
    hr: {
      title: "HR (Disha Damwani)",
      name: "Disha Damwani",
      color: "#7c3aed",
      desc: "Employee docs + Confidential compensation, salary bands L1-L6, promotion rules.",
      badgeVariant: "purple",
    },
    operations: {
      title: "Operations (Zainab Husain)",
      name: "Zainab Husain",
      color: "#0891b2",
      desc: "Employee docs + Operations workflows, FBA fulfillment, department structures.",
      badgeVariant: "cyan",
    },
    employee: {
      title: "Employee (Vivek Parihar)",
      name: "Vivek Parihar",
      color: "#0284c7",
      desc: "General public policies, FAQs, service catalogs, job openings, leave guidelines.",
      badgeVariant: "blue",
    },
    it: {
      title: "IT (Ali Asgar)",
      name: "Ali Asgar",
      color: "#059669",
      desc: "General docs + IT infrastructure, security guidelines, system administration.",
      badgeVariant: "green",
    },
    finance: {
      title: "Finance & Accounts",
      name: "Finance Team",
      color: "#d97706",
      desc: "General docs + Commercial billing, invoice records, pricing plans.",
      badgeVariant: "amber",
    },
  };

  const enableDevAuth = resolveDevAuthEnabled(
    Boolean(import.meta.env.DEV),
    import.meta.env.VITE_ENABLE_DEV_AUTH
  );

  return (
    <WorkspaceShell
      apiUrl={API_URL}
      enableDevAuth={enableDevAuth}
      getActiveToken={getActiveToken}
      activeRole={activeRole}
      tokens={tokens}
      pastedInput={pastedInput}
      setPastedInput={setPastedInput}
      onAssignRole={handleAssignRole}
      onAutoAssign={handleAutoAssign}
      onSelectRole={(role) => setActiveRole(role)}
      onSignOut={handleSignOut}
      detectedRole={detected}
      activeTokenClaims={activeTokenClaims}
      healthInfo={healthInfo}
      onRefreshHealth={refreshHealth}
      isSyncing={isSyncing}
      syncStatus={syncStatus}
      lastSyncTime={lastSyncTime}
      lastSyncChunks={lastSyncChunks}
      onSyncMongo={handleSyncMongo}
      roleLabels={roleLabels}
    />
  );
};
