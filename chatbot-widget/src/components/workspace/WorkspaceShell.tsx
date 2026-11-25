/**
 * Fakhri ERP AI — Full-Screen Enterprise Workspace Shell (Phase 6 Architecture)
 * Integrates Area A (Sidebar with Date Grouping), Area B (Main Canvas), Area C (Contextual Panel),
 * User Profile Experience, Protected Admin & Developer Settings Modal, Search Modal, and Action Dialogs.
 */
import React, { useState, useEffect } from "react";
import {
  FakhriLogoIcon,
  SparklesIcon,
  ComposeIcon,
  PanelLeftIcon,
  InfoIcon,
  DatabaseIcon,
  TerminalIcon,
  UserIcon,
  SearchIcon,
  HelpCircleIcon,
  SettingsIcon,
  PencilIcon,
  TrashIcon,
  PinIcon,
  XIcon,
  TemporaryChatIcon,
  ChevronDownIcon,
  ChevronsUpDownIcon,
} from "../ui/Icons";
import { ToastProvider, useToast } from "../ui/Toast";
import { Badge } from "../ui/Badge";
import { IconButton } from "../ui/IconButton";
import { ChatWidget } from "../ChatWidget";
import { ContextualPanel } from "./ContextualPanel";
import { HelpSupportModal } from "./HelpSupportModal";
import { SearchModal } from "./SearchModal";
import { RenameModal } from "./RenameModal";
import { DeleteConfirmModal } from "./DeleteConfirmModal";
import { UserProfileModal } from "./UserProfileModal";
import { AdminSettingsModal } from "./AdminSettingsModal";
import { EntityExplorerModal } from "./EntityExplorerModal";
import {
  StoredConversation,
  loadStoredSessions,
  saveStoredSessions,
  groupConversationsByDate,
  formatConversationTime,
} from "../../services/sessionStore";
import { UIMessage } from "../../types/chat";
import { canOpenAdminWorkspaceSettings, resolveDevAuthEnabled } from "../../services/securityGate";

export type RoleType = "super-admin" | "founder" | "hr" | "operations" | "it" | "finance" | "employee";

export interface PersonaInfo {
  title: string;
  name: string;
  color: string;
  desc: string;
  badgeVariant: "red" | "purple" | "cyan" | "blue" | "green" | "amber" | "slate";
}

export interface HealthTelemetry {
  status: "online" | "offline" | "checking";
  orchestrator?: string;
  llm_provider?: string;
  version?: string;
  checkedAt?: string;
}

export interface WorkspaceShellProps {
  apiUrl: string;
  getActiveToken: () => string;
  activeRole: RoleType | "none";
  tokens: Record<RoleType, string>;
  pastedInput: string;
  setPastedInput: (val: string) => void;
  onAssignRole: (role: RoleType) => void;
  onAutoAssign: () => void;
  onSelectRole: (role: RoleType) => void;
  onSignOut?: () => void;
  detectedRole: { role?: string; sub?: string; exp?: number } | null;
  activeTokenClaims?: { role?: string; sub?: string; exp?: number } | null;
  healthInfo?: HealthTelemetry;
  onRefreshHealth?: () => void;
  isSyncing: boolean;
  syncStatus: string | null;
  lastSyncTime?: string | null;
  lastSyncChunks?: number | null;
  onSyncMongo: () => Promise<void>;
  roleLabels: Record<RoleType, PersonaInfo>;
  enableDevAuth?: boolean;
}

const IS_DEV_ENV = resolveDevAuthEnabled(
  Boolean((import.meta as { env?: { DEV?: boolean } }).env?.DEV),
  (import.meta as { env?: { VITE_ENABLE_DEV_AUTH?: string } }).env?.VITE_ENABLE_DEV_AUTH
);

const WorkspaceShellInner: React.FC<WorkspaceShellProps> = ({
  apiUrl,
  getActiveToken,
  activeRole,
  tokens,
  pastedInput,
  setPastedInput,
  onAssignRole,
  onAutoAssign,
  onSelectRole,
  onSignOut,
  detectedRole,
  activeTokenClaims = null,
  healthInfo,
  isSyncing,
  syncStatus,
  lastSyncTime = null,
  lastSyncChunks = null,
  onSyncMongo,
  roleLabels,
  enableDevAuth = IS_DEV_ENV,
}) => {
  const canOpenAdminSettings = canOpenAdminWorkspaceSettings(enableDevAuth, activeRole);
  const { showToast } = useToast();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [contextualPanelOpen, setContextualPanelOpen] = useState(false);
  const [helpModalOpen, setHelpModalOpen] = useState(false);
  const [searchModalOpen, setSearchModalOpen] = useState(false);
  const [entityExplorerOpen, setEntityExplorerOpen] = useState(false);
  const [pendingPrompt, setPendingPrompt] = useState<{ id: string; text: string } | null>(null);

  // Phase 6: User Profile & Protected Admin Settings Modals
  const [userProfileModalOpen, setUserProfileModalOpen] = useState(false);
  const [adminSettingsModalOpen, setAdminSettingsModalOpen] = useState(false);

  // Conversation and Session Persistence Store (role-scoped to prevent cross-role leakage)
  const [conversations, setConversations] = useState<StoredConversation[]>(() => {
    const loaded = loadStoredSessions();
    const roleMatches = loaded.filter((c) => c.role === activeRole);
    if (roleMatches.length > 0) return loaded;
    const now = new Date().toISOString();
    const initial: StoredConversation = {
      id: `session-init-${Date.now()}`,
      title: "New Conversation",
      role: activeRole,
      createdAt: now,
      updatedAt: now,
      messages: [],
    };
    const next = [initial, ...loaded];
    saveStoredSessions(next);
    return next;
  });

  const [activeSessionId, setActiveSessionId] = useState<string>(() => {
    const roleMatches = conversations.filter((c) => c.role === activeRole);
    return roleMatches[0]?.id || conversations[0]?.id || `session-init-${Date.now()}`;
  });

  // Switch active thread cleanly when user switches persona/role (guards backend IDOR + local leakage)
  useEffect(() => {
    const matchingForRole = conversations.filter((c) => c.role === activeRole);
    const currentValid = matchingForRole.some((c) => c.id === activeSessionId);
    if (!currentValid) {
      if (matchingForRole.length > 0) {
        setActiveSessionId(matchingForRole[0].id);
      } else {
        const now = new Date().toISOString();
        const freshId = `session-${Date.now()}`;
        const freshConv: StoredConversation = {
          id: freshId,
          title: "New Conversation",
          role: activeRole,
          createdAt: now,
          updatedAt: now,
          messages: [],
        };
        setConversations((prev) => {
          const next = [freshConv, ...prev];
          saveStoredSessions(next);
          return next;
        });
        setActiveSessionId(freshId);
      }
    }
  }, [activeRole, activeSessionId, conversations]);

  // Ephemeral Temporary Chat state (held strictly in RAM; never saved to localStorage or MongoDB)
  const [isTemporaryChat, setIsTemporaryChat] = useState(false);
  const [tempThreadId, setTempThreadId] = useState<string>("temp-initial");
  const [tempMessages, setTempMessages] = useState<UIMessage[]>([]);

  useEffect(() => {
    setIsTemporaryChat(false);
    setTempMessages([]);
  }, [activeRole]);

  // Modals for conversation actions
  const [targetRenameConv, setTargetRenameConv] = useState<StoredConversation | null>(null);
  const [targetDeleteConv, setTargetDeleteConv] = useState<StoredConversation | null>(null);

  const activePersona: PersonaInfo | null =
    activeRole !== "none" && activeRole in roleLabels ? roleLabels[activeRole] : null;

  const activeToken = getActiveToken();

  const visibleConversations = conversations.filter((c) => c.role === activeRole);

  const activeConversation: StoredConversation | undefined =
    visibleConversations.find((c) => c.id === activeSessionId) || visibleConversations[0];

  const handleToggleTemporaryChat = () => {
    setIsTemporaryChat((prev) => {
      const next = !prev;
      setTempMessages([]);
      if (next) {
        setTempThreadId(`temp-${Date.now()}`);
        setMobileSidebarOpen(false);
        showToast("Temporary Chat active — won't appear in history", "info");
      } else {
        showToast("Exited Temporary Chat", "info");
      }
      return next;
    });
  };

  const handleTemporarySessionChange = (sessionData: {
    id: string;
    backendSessionId?: string;
    messages: UIMessage[];
    title?: string;
  }) => {
    setTempMessages(sessionData.messages);
  };

  const handleSelectConversation = (id: string) => {
    setIsTemporaryChat(false);
    setTempMessages([]);
    setActiveSessionId(id);
    setMobileSidebarOpen(false);
  };

  const handleNewChat = () => {
    setIsTemporaryChat(false);
    setTempMessages([]);
    const now = new Date().toISOString();
    const newSid = `session-${Date.now()}`;
    const newConv: StoredConversation = {
      id: newSid,
      title: "New Conversation",
      role: activeRole,
      createdAt: now,
      updatedAt: now,
      messages: [],
    };

    setConversations((prev) => {
      const next = [newConv, ...prev];
      saveStoredSessions(next);
      return next;
    });

    setActiveSessionId(newSid);
    setMobileSidebarOpen(false);
    showToast("New conversation started", "info");
  };

  const handleSessionChange = (sessionData: {
    id: string;
    backendSessionId?: string;
    messages: UIMessage[];
    title?: string;
  }) => {
    const targetId = activeConversation?.id || activeSessionId;
    setConversations((prev) => {
      const exists = prev.some((c) => c.id === targetId);
      const now = new Date().toISOString();
      let next: StoredConversation[];

      if (exists) {
        next = prev.map((c) => {
          if (c.id === targetId) {
            const hasCustomTitle =
              c.title !== "New Conversation" && c.title !== "Active Knowledge Session";
            return {
              ...c,
              backendSessionId: sessionData.backendSessionId ?? c.backendSessionId,
              messages: sessionData.messages,
              title: hasCustomTitle ? c.title : (sessionData.title || c.title),
              updatedAt: now,
            };
          }
          return c;
        });
      } else {
        const newConv: StoredConversation = {
          id: targetId,
          backendSessionId: sessionData.backendSessionId,
          title: sessionData.title || "New Conversation",
          role: activeRole,
          createdAt: now,
          updatedAt: now,
          messages: sessionData.messages,
        };
        next = [newConv, ...prev];
      }

      saveStoredSessions(next);
      return next;
    });
  };

  const handleTogglePin = (convId: string) => {
    setConversations((prev) => {
      const target = prev.find((c) => c.id === convId);
      const isPinned = target?.pinned;
      const next = prev.map((c) => (c.id === convId ? { ...c, pinned: !c.pinned } : c));
      saveStoredSessions(next);
      showToast(isPinned ? "Conversation unpinned" : "Conversation pinned to top", "info");
      return next;
    });
  };

  const handleRenameConfirm = (newTitle: string) => {
    if (!targetRenameConv) return;
    setConversations((prev) => {
      const next = prev.map((c) => (c.id === targetRenameConv.id ? { ...c, title: newTitle } : c));
      saveStoredSessions(next);
      showToast(`Conversation renamed to "${newTitle}"`, "success");
      return next;
    });
    setTargetRenameConv(null);
  };

  const handleDeleteConfirm = () => {
    if (!targetDeleteConv) return;
    const deletedId = targetDeleteConv.id;
    setConversations((prev) => {
      const next = prev.filter((c) => c.id !== deletedId);
      const nextForRole = next.filter((c) => c.role === activeRole);
      showToast("Conversation deleted", "warning");

      if (activeSessionId === deletedId) {
        if (nextForRole.length > 0) {
          saveStoredSessions(next);
          setActiveSessionId(nextForRole[0].id);
          return next;
        } else {
          // Re-create a fresh thread for active role without disturbing other role caches
          const now = new Date().toISOString();
          const freshId = `session-${Date.now()}`;
          const freshConv: StoredConversation = {
            id: freshId,
            title: "New Conversation",
            role: activeRole,
            createdAt: now,
            updatedAt: now,
            messages: [],
          };
          const updatedAll = [freshConv, ...next];
          saveStoredSessions(updatedAll);
          setActiveSessionId(freshId);
          return updatedAll;
        }
      }
      saveStoredSessions(next);
      return next;
    });
    setTargetDeleteConv(null);
  };

  // Keyboard shortcut listener for Cmd+K (New Chat), Cmd+F (Search), and Esc (Close all)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        handleNewChat();
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "f") {
        e.preventDefault();
        setSearchModalOpen(true);
      } else if (e.key === "Escape") {
        setAdminSettingsModalOpen(false);
        setUserProfileModalOpen(false);
        setHelpModalOpen(false);
        setSearchModalOpen(false);
        setTargetRenameConv(null);
        setTargetDeleteConv(null);
        setMobileSidebarOpen(false);
        setContextualPanelOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [activeRole, activeSessionId]);

  const getInitials = (name: string): string => {
    if (!name) return "U";
    const parts = name.split(" ").filter(Boolean);
    if (parts.length >= 2) {
      return (parts[0][0] + parts[1][0]).toUpperCase();
    }
    return name.slice(0, 2).toUpperCase();
  };

  const { pinned, groups } = groupConversationsByDate(visibleConversations);

  return (
    <div className="fkh-workspace-root">
      {/* Mobile & Tablet Backdrop Overlay */}
      {(mobileSidebarOpen || contextualPanelOpen) && (
        <div
          className="fkh-mobile-backdrop"
          onClick={() => {
            setMobileSidebarOpen(false);
            setContextualPanelOpen(false);
          }}
          aria-hidden="true"
        />
      )}

      {/* ====================================================================
          AREA A: LEFT NAVIGATION SIDEBAR (OpenAI & Google Gemini Inspired)
          ==================================================================== */}
      <aside
        className={`fkh-sidebar ${sidebarCollapsed ? "collapsed" : ""} ${
          mobileSidebarOpen ? "mobile-open" : ""
        }`}
        aria-label="Application Navigation"
      >
        {/* Brand Header */}
        <div className="fkh-sidebar-header">
          <div className="fkh-brand-lockup" title="Fakhri AI">
            <div className="fkh-brand-icon-box">
              <FakhriLogoIcon size={17} />
            </div>
            <span className="fkh-brand-title">Fakhri AI</span>
          </div>
          <div className="fkh-sidebar-header-actions">
            <IconButton
              icon={<PanelLeftIcon size={18} />}
              title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-expanded={!sidebarCollapsed}
              onClick={() => setSidebarCollapsed((prev) => !prev)}
              variant="ghost"
              size="md"
              className="fkh-desktop-only fkh-header-icon-btn fkh-sidebar-toggle-btn"
            />
            <IconButton
              icon={<XIcon size={18} />}
              title="Close Navigation"
              aria-label="Close Navigation"
              onClick={() => setMobileSidebarOpen(false)}
              size="sm"
              className="fkh-mobile-only"
            />
          </div>
        </div>

        {/* Fixed Primary Actions: New Chat & Search */}
        <div className="fkh-sidebar-action-area">
          <button
            type="button"
            className="fkh-new-chat-btn"
            onClick={handleNewChat}
            title="New chat (Ctrl+K)"
            aria-label="New chat"
            data-tooltip="New chat (⌘K)"
          >
            <span className="fkh-new-chat-left">
              <span className="fkh-new-chat-icon-wrap">
                <ComposeIcon size={16} />
              </span>
              <span className="fkh-new-chat-label">New chat</span>
            </span>
            <span className="fkh-shortcut-hint" aria-hidden="true">⌘K</span>
          </button>

          <button
            type="button"
            className="fkh-nav-item fkh-sidebar-search-btn"
            onClick={() => setSearchModalOpen(true)}
            title="Search chats and enterprise topics (Ctrl+F)"
            aria-label="Search chats"
            data-tooltip="Search chats (⌘F)"
          >
            <span className="fkh-nav-item-left">
              <SearchIcon size={16} />
              <span className="fkh-nav-item-label">Search chats</span>
            </span>
            <span className="fkh-shortcut-hint" aria-hidden="true">⌘F</span>
          </button>

          <button
            type="button"
            className="fkh-nav-item fkh-sidebar-search-btn"
            onClick={() => setEntityExplorerOpen(true)}
            title="Dynamic Entity Explorer & Relationship Graph"
            aria-label="Open Entity Explorer"
            data-tooltip="Entity Explorer"
          >
            <span className="fkh-nav-item-left">
              <DatabaseIcon size={16} />
              <span className="fkh-nav-item-label">Entity Explorer</span>
            </span>
          </button>
        </div>

        {/* Navigation Body (Chronological Conversation History) */}
        <nav className="fkh-sidebar-nav" aria-label="Chat history">
          {/* Pinned Section */}
          {pinned.length > 0 && (
            <div className="fkh-nav-group-section">
              <span className="fkh-nav-section-title">Pinned</span>
              {pinned.map((conv) => (
                <div
                  key={conv.id}
                  className={`fkh-sidebar-conv-row ${conv.id === activeSessionId ? "active" : ""}`}
                  data-tooltip={conv.title}
                >
                  <button
                    type="button"
                    className="fkh-conv-select-btn"
                    title={`${conv.title} · ${formatConversationTime(conv.updatedAt || conv.createdAt)}`}
                    onClick={() => handleSelectConversation(conv.id)}
                  >
                    <PinIcon size={13} className="fkh-pinned-thread-icon" />
                    <span className="fkh-conv-title-text">{conv.title}</span>
                  </button>

                  <div className="fkh-conv-actions-wrap">
                    <button
                      type="button"
                      className="fkh-conv-action-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleTogglePin(conv.id);
                      }}
                      title="Unpin conversation"
                      aria-label="Unpin conversation"
                    >
                      <PinIcon size={13} />
                    </button>
                    <button
                      type="button"
                      className="fkh-conv-action-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        setTargetRenameConv(conv);
                      }}
                      title="Rename conversation"
                      aria-label="Rename conversation"
                    >
                      <PencilIcon size={13} />
                    </button>
                    <button
                      type="button"
                      className="fkh-conv-action-btn fkh-action-danger"
                      onClick={(e) => {
                        e.stopPropagation();
                        setTargetDeleteConv(conv);
                      }}
                      title="Delete conversation"
                      aria-label="Delete conversation"
                    >
                      <TrashIcon size={13} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Chronological Groups: Today, Yesterday, Previous 7 Days, etc. */}
          {groups.map((group) => (
            <div key={group.label} className="fkh-nav-group-section">
              <span className="fkh-nav-section-title">{group.label}</span>
              {group.items.map((conv) => (
                <div
                  key={conv.id}
                  className={`fkh-sidebar-conv-row ${conv.id === activeSessionId ? "active" : ""}`}
                  data-tooltip={conv.title}
                >
                  <button
                    type="button"
                    className="fkh-conv-select-btn"
                    title={`${conv.title} · ${formatConversationTime(conv.updatedAt || conv.createdAt)}`}
                    onClick={() => handleSelectConversation(conv.id)}
                  >
                    <span className="fkh-conv-title-text">{conv.title}</span>
                  </button>

                  <div className="fkh-conv-actions-wrap">
                    <button
                      type="button"
                      className="fkh-conv-action-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleTogglePin(conv.id);
                      }}
                      title="Pin conversation"
                      aria-label="Pin conversation"
                    >
                      <PinIcon size={13} />
                    </button>
                    <button
                      type="button"
                      className="fkh-conv-action-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        setTargetRenameConv(conv);
                      }}
                      title="Rename conversation"
                      aria-label="Rename conversation"
                    >
                      <PencilIcon size={13} />
                    </button>
                    <button
                      type="button"
                      className="fkh-conv-action-btn fkh-action-danger"
                      onClick={(e) => {
                        e.stopPropagation();
                        setTargetDeleteConv(conv);
                      }}
                      title="Delete conversation"
                      aria-label="Delete conversation"
                    >
                      <TrashIcon size={13} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ))}

          {/* Empty History State */}
          {visibleConversations.length === 0 && (
            <div className="fkh-sidebar-empty-threads">
              <p className="fkh-sidebar-empty-text">
                No conversations yet. Click &quot;New chat&quot; or start typing to begin.
              </p>
            </div>
          )}
        </nav>

        {/* Sidebar Footer */}
        <div className="fkh-sidebar-footer">
          {/* Help & Support CTA */}
          <button
            type="button"
            className="fkh-nav-item fkh-footer-nav-item"
            onClick={() => setHelpModalOpen(true)}
            title="Help, Department Inquiries & Shortcuts"
            data-tooltip="Help & Support"
          >
            <HelpCircleIcon size={16} />
            <span className="fkh-nav-item-label">Help & Support</span>
          </button>

          {/* Settings & Dev Controls CTA */}
          {canOpenAdminSettings && (
            <button
              type="button"
              className="fkh-nav-item fkh-footer-nav-item"
              onClick={() => setAdminSettingsModalOpen(true)}
              title={enableDevAuth ? "Protected Admin & Developer Settings" : "Admin Knowledge Base Sync"}
              data-tooltip={enableDevAuth ? "Settings & Roles" : "Admin Settings"}
            >
              <SettingsIcon size={16} />
              <span className="fkh-nav-item-label">{enableDevAuth ? "Settings & Roles" : "Admin Settings"}</span>
            </button>
          )}

          {/* Active User Profile Bar */}
          <button
            type="button"
            className={`fkh-user-profile-badge fkh-profile-clickable ${!activePersona ? "is-guest" : ""}`}
            onClick={() => setUserProfileModalOpen(true)}
            title={activePersona ? `${activePersona.name} (${activePersona.title})` : "View Profile & Role"}
            aria-label={activePersona ? `User profile: ${activePersona.name}` : "View user profile"}
            data-tooltip={activePersona ? `${activePersona.name} (${activeRole.toUpperCase()})` : "Unauthenticated"}
          >
            <div
              className={`fkh-user-avatar ${!activePersona ? "fkh-user-avatar-guest" : ""}`}
              style={activePersona ? { backgroundColor: activePersona.color } : undefined}
            >
              {activePersona ? getInitials(activePersona.name) : <UserIcon size={15} />}
            </div>
            <div className="fkh-user-info">
              <span className="fkh-user-name">
                {activePersona ? activePersona.name : "Unauthenticated"}
              </span>
              <span className="fkh-user-role-label">
                {activePersona
                  ? activePersona.title.split("(")[0].trim()
                  : enableDevAuth
                  ? "Select Persona"
                  : "Host Session"}
              </span>
            </div>
            <span className="fkh-user-chevron" aria-hidden="true">
              <ChevronsUpDownIcon size={14} />
            </span>
          </button>
        </div>
      </aside>

      {/* ====================================================================
          AREA B: MAIN WORKSPACE CONTAINER
          ==================================================================== */}
      <main className="fkh-main-workspace" role="main">
        {/* Workspace Header */}
        <header className="fkh-workspace-header">
          <div className="fkh-header-left">
            <IconButton
              icon={<PanelLeftIcon size={18} />}
              title="Open Navigation"
              aria-label="Open Navigation Menu"
              aria-expanded={mobileSidebarOpen}
              onClick={() => setMobileSidebarOpen((prev) => !prev)}
              size="sm"
              className="fkh-mobile-only"
            />
            <div className="fkh-header-brand-group">
              <button
                type="button"
                className="fkh-header-model-pill"
                onClick={() => setContextualPanelOpen((prev) => !prev)}
                title="View Fakhri AI Workspace Scope"
                aria-label="Fakhri AI Workspace Scope"
                aria-expanded={contextualPanelOpen}
              >
                <span className="fkh-header-model-emblem" aria-hidden="true">
                  <SparklesIcon size={13} />
                </span>
                <span className="fkh-header-model-name">Fakhri AI</span>
                <span className="fkh-header-model-tier">
                  {healthInfo?.status === "online" && (
                    <span className="fkh-header-live-dot" aria-hidden="true" />
                  )}
                  <span>Enterprise</span>
                </span>
                <ChevronDownIcon size={13} className="fkh-header-pill-chevron" />
              </button>

              {isTemporaryChat ? (
                <span className="fkh-temp-mode-chip" title="Not saved to history or server sessions">
                  Temporary · Not Saved
                </span>
              ) : (
                activeConversation &&
                activeConversation.title !== "New Conversation" && (
                  <>
                    <span className="fkh-header-dot-sep" aria-hidden="true">
                      /
                    </span>
                    <span className="fkh-header-thread-title" title={activeConversation.title}>
                      {activeConversation.title}
                    </span>
                  </>
                )
              )}
            </div>
          </div>

          <div className="fkh-header-right">
            {healthInfo?.status === "offline" && (
              <div className="fkh-header-badge-group fkh-hide-on-mobile">
                <Badge variant="red" size="md">
                  API Offline
                </Badge>
              </div>
            )}

            <button
              type="button"
              className={`fkh-header-temp-pill ${isTemporaryChat ? "is-active" : ""}`}
              onClick={handleToggleTemporaryChat}
              title={isTemporaryChat ? "Exit Temporary Chat" : "Temporary Chat (Not Saved)"}
              aria-label={isTemporaryChat ? "Exit Temporary Chat" : "Start Temporary Chat"}
              aria-pressed={isTemporaryChat}
            >
              <TemporaryChatIcon size={15} />
              <span className="fkh-header-temp-label">Temporary</span>
            </button>

            <div className="fkh-header-actions-capsule" role="group" aria-label="Workspace controls">
              <IconButton
                icon={<DatabaseIcon size={16} />}
                title="Open Dynamic Entity Explorer"
                aria-label="Open Dynamic Entity Explorer"
                aria-expanded={entityExplorerOpen}
                onClick={() => setEntityExplorerOpen(true)}
                variant={entityExplorerOpen ? "primary" : "ghost"}
                size="sm"
                className="fkh-header-capsule-btn"
              />
              <span className="fkh-capsule-divider" aria-hidden="true" />
              <IconButton
                icon={<InfoIcon size={16} />}
                title={contextualPanelOpen ? "Close Contextual Details" : "Open Contextual Details"}
                aria-label={contextualPanelOpen ? "Close Contextual Details" : "Open Contextual Details"}
                aria-expanded={contextualPanelOpen}
                onClick={() => setContextualPanelOpen((prev) => !prev)}
                variant={contextualPanelOpen ? "primary" : "ghost"}
                size="sm"
                className="fkh-header-capsule-btn"
              />

              {canOpenAdminSettings && (
                <>
                  <span className="fkh-capsule-divider" aria-hidden="true" />
                  <IconButton
                    icon={<TerminalIcon size={16} />}
                    title={enableDevAuth ? "Open Admin & Developer Settings" : "Open Admin Workspace Settings"}
                    aria-label={enableDevAuth ? "Open Admin & Developer Settings" : "Open Admin Workspace Settings"}
                    onClick={() => setAdminSettingsModalOpen(true)}
                    variant="ghost"
                    size="sm"
                    className="fkh-header-capsule-btn"
                  />
                </>
              )}
            </div>

            {/* Far-right OpenAI / Google style Account & Role Pill */}
            {activePersona ? (
              <button
                type="button"
                className="fkh-header-account-pill"
                onClick={() => setUserProfileModalOpen(true)}
                title={`${activePersona.name} — View Profile and Role Details`}
                aria-label={`View Profile: ${activePersona.name} (${activeRole.toUpperCase()})`}
              >
                <span
                  className="fkh-account-pill-avatar is-authenticated"
                  style={{ backgroundColor: activePersona.color }}
                  aria-hidden="true"
                >
                  {getInitials(activePersona.name)}
                </span>
                <span className="fkh-account-pill-meta">
                  <span className="fkh-account-pill-name">{activePersona.name}</span>
                  <span className="fkh-account-pill-role">{activeRole.replace("-", " ")}</span>
                </span>
                <ChevronDownIcon size={13} className="fkh-header-pill-chevron fkh-hide-on-mobile" />
              </button>
            ) : enableDevAuth ? (
              <button
                type="button"
                className="fkh-header-account-pill fkh-account-pill-signin"
                onClick={() => setAdminSettingsModalOpen(true)}
                title="Select Role / Set JWT Persona"
                aria-label="Select Role Persona"
              >
                <span className="fkh-account-pill-avatar fkh-account-avatar-guest" aria-hidden="true">
                  <UserIcon size={14} />
                </span>
                <span className="fkh-account-pill-meta">
                  <span className="fkh-account-pill-name">Select Role</span>
                </span>
                <ChevronDownIcon size={13} className="fkh-header-pill-chevron fkh-hide-on-mobile" />
              </button>
            ) : (
              <span className="fkh-header-account-pill fkh-account-pill-static">
                <span className="fkh-account-pill-avatar fkh-account-avatar-guest" aria-hidden="true">
                  <UserIcon size={14} />
                </span>
                <span className="fkh-account-pill-meta">
                  <span className="fkh-account-pill-name">Session Required</span>
                </span>
              </span>
            )}
          </div>
        </header>

        {/* Embedded Full-Screen Chat Canvas */}
        <div className="fkh-conversation-canvas">
          <ChatWidget
            key={isTemporaryChat ? tempThreadId : activeConversation?.id}
            apiUrl={apiUrl}
            getToken={getActiveToken}
            embedded={true}
            isTemporary={isTemporaryChat}
            activePersonaName={activePersona?.name}
            activePersonaTitle={activePersona?.title}
            activePersonaRole={activeRole}
            activePersonaColor={activePersona?.color}
            onOpenDevTools={enableDevAuth ? () => setAdminSettingsModalOpen(true) : undefined}
            sessionId={isTemporaryChat ? undefined : activeConversation?.backendSessionId}
            initialMessages={isTemporaryChat ? tempMessages : (activeConversation?.messages || [])}
            onSessionChange={isTemporaryChat ? handleTemporarySessionChange : handleSessionChange}
            onNewChatTriggered={handleNewChat}
            pendingPrompt={pendingPrompt}
            onPendingPromptConsumed={() => setPendingPrompt(null)}
          />
        </div>
      </main>

      {/* ====================================================================
          AREA C: COLLAPSIBLE CONTEXTUAL PANEL ARCHITECTURE
          ==================================================================== */}
      <ContextualPanel
        isOpen={contextualPanelOpen}
        onClose={() => setContextualPanelOpen(false)}
        activeRole={activeRole}
        activePersona={activePersona}
        activeToken={activeToken}
        isTemporary={isTemporaryChat}
        activeConversation={
          isTemporaryChat
            ? {
                id: tempThreadId,
                title: "Temporary Chat",
                role: activeRole,
                createdAt: "",
                updatedAt: "",
                messages: tempMessages,
              }
            : activeConversation
        }
        lastSyncTime={lastSyncTime}
        lastSyncChunks={lastSyncChunks}
        healthInfo={healthInfo}
        activeTokenClaims={activeTokenClaims}
      />

      {/* ====================================================================
          MODALS & ENTERPRISE CONTROLS (Phase 6 Architecture)
          ==================================================================== */}
      {/* 1. User Profile & Identity Modal */}
      <UserProfileModal
        isOpen={userProfileModalOpen}
        onClose={() => setUserProfileModalOpen(false)}
        activeRole={activeRole}
        activePersona={activePersona}
        userSub={activeTokenClaims?.sub || detectedRole?.sub}
        enableDevAuth={enableDevAuth}
        onOpenSettings={() => setAdminSettingsModalOpen(true)}
        onSignOut={() => {
          setIsTemporaryChat(false);
          setTempMessages([]);
          onSignOut?.();
          showToast("Signed out of active session", "info");
        }}
      />

      {/* 2. Protected Admin & Developer Settings Modal */}
      {canOpenAdminSettings && (
        <AdminSettingsModal
          isOpen={adminSettingsModalOpen}
          onClose={() => setAdminSettingsModalOpen(false)}
          activeRole={activeRole}
          tokens={tokens}
          pastedInput={pastedInput}
          setPastedInput={setPastedInput}
          onAssignRole={onAssignRole}
          onAutoAssign={onAutoAssign}
          onSelectRole={(role) => {
            onSelectRole(role);
            showToast(`Switched active context to ${roleLabels[role].name}`, "success");
          }}
          detectedRole={detectedRole}
          activeTokenClaims={activeTokenClaims}
          healthInfo={healthInfo}
          isSyncing={isSyncing}
          syncStatus={syncStatus}
          lastSyncTime={lastSyncTime}
          lastSyncChunks={lastSyncChunks}
          onSyncMongo={onSyncMongo}
          roleLabels={roleLabels}
          enableDevAuth={enableDevAuth}
        />
      )}

      {/* 3. Help & Support Modal */}
      <HelpSupportModal
        isOpen={helpModalOpen}
        onClose={() => setHelpModalOpen(false)}
      />

      {/* 4. Unified Search Conversations & Topics Modal */}
      <SearchModal
        isOpen={searchModalOpen}
        activeRole={activeRole}
        conversations={visibleConversations}
        onClose={() => setSearchModalOpen(false)}
        onSelectQuery={(q) => {
          setPendingPrompt({ id: String(Date.now()), text: q });
        }}
        onSelectConversation={handleSelectConversation}
      />

      {/* 5. Rename Conversation Modal */}
      <RenameModal
        isOpen={targetRenameConv !== null}
        currentTitle={targetRenameConv?.title || ""}
        onClose={() => setTargetRenameConv(null)}
        onSave={handleRenameConfirm}
      />

      {/* 6. Delete Conversation Confirmation Modal */}
      <DeleteConfirmModal
        isOpen={targetDeleteConv !== null}
        conversationTitle={targetDeleteConv?.title || ""}
        onClose={() => setTargetDeleteConv(null)}
        onConfirm={handleDeleteConfirm}
      />

      {/* 7. Dynamic Entity Intelligence & Relationship Explorer Modal */}
      <EntityExplorerModal
        isOpen={entityExplorerOpen}
        apiUrl={apiUrl}
        authToken={getActiveToken()}
        activeRole={activeRole}
        onClose={() => setEntityExplorerOpen(false)}
        onAskInChat={(q) => setPendingPrompt({ id: String(Date.now()), text: q })}
      />
    </div>
  );
};

export const WorkspaceShell: React.FC<WorkspaceShellProps> = (props) => (
  <ToastProvider>
    <WorkspaceShellInner {...props} />
  </ToastProvider>
);
