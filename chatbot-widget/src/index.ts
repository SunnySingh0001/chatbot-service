/**
 * Public exports for the Chatbot Widget library and Workspace Shell.
 */
export { ChatWidget } from "./components/ChatWidget";
export { SafeMarkdown } from "./components/SafeMarkdown";
export { WorkspaceShell } from "./components/workspace/WorkspaceShell";
export { ContextualPanel } from "./components/workspace/ContextualPanel";
export { HelpSupportModal } from "./components/workspace/HelpSupportModal";
export { SearchModal } from "./components/workspace/SearchModal";
export { RenameModal } from "./components/workspace/RenameModal";
export { DeleteConfirmModal } from "./components/workspace/DeleteConfirmModal";
export { UserProfileModal } from "./components/workspace/UserProfileModal";
export { AdminSettingsModal } from "./components/workspace/AdminSettingsModal";
export type { StoredConversation } from "./services/sessionStore";
export { groupConversationsByDate, searchConversations } from "./services/sessionStore";
export { STARTER_PROMPTS, getVisiblePrompts, getRoleAccessibleModules } from "./services/promptStarters";
export type { WorkspaceShellProps, RoleType, PersonaInfo, HealthTelemetry } from "./components/workspace/WorkspaceShell";
export { Button } from "./components/ui/Button";
export { IconButton } from "./components/ui/IconButton";
export { Badge } from "./components/ui/Badge";
export { ToastProvider, useToast } from "./components/ui/Toast";
export type { ToastType, ToastItem } from "./components/ui/Toast";
export type { ChatWidgetProps, UIMessage, Role, BackendChatMessage } from "./types/chat";
import "./styles/widget.css";
import "./styles/workspace.css";
