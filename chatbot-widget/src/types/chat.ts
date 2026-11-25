/**
 * Core type definitions for the chat widget, matching backend schema constraints.
 */

export type Role = "user" | "assistant";

export interface BackendChatMessage {
  role: Role;
  content: string;
}

export interface ChatRequestPayload {
  message: string;
  history?: BackendChatMessage[];
  session_id?: string;
  temporary?: boolean;
}

export interface UIMessage {
  id: string;
  role: Role;
  content: string;
  sources?: string[];
  isStreaming?: boolean;
  error?: string;
  feedback?: "up" | "down";
  timestamp?: string;
}

export interface ChatWidgetProps {
  apiUrl: string;
  getToken: () => string | Promise<string>;
  embedded?: boolean;
  activePersonaName?: string;
  activePersonaTitle?: string;
  activePersonaRole?: string;
  activePersonaColor?: string;
  onOpenDevTools?: () => void;
  onSelectPrompt?: (prompt: string) => void;
  sessionId?: string;
  initialMessages?: UIMessage[];
  onSessionChange?: (session: {
    id: string;
    backendSessionId?: string;
    messages: UIMessage[];
    title?: string;
  }) => void;
  onNewChatTriggered?: () => void;
  pendingPrompt?: { id: string; text: string } | null;
  onPendingPromptConsumed?: () => void;
  onPersistFeedback?: (messageId: string, type: "up" | "down" | null) => Promise<void> | void;
  isTemporary?: boolean;
}

export interface SSETokenEvent {
  token?: string;
  done?: boolean;
  sources?: string[];
  session_id?: string;
  temporary?: boolean;
  error?: boolean;
  code?: string;
  message?: string;
  request_id?: string;
}
