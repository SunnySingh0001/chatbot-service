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
  history: BackendChatMessage[];
}

export interface UIMessage {
  id: string;
  role: Role;
  content: string;
  sources?: string[];
  isStreaming?: boolean;
  error?: string;
  feedback?: "up" | "down";
}

export interface ChatWidgetProps {
  apiUrl: string;
  getToken: () => string | Promise<string>;
}

export interface SSETokenEvent {
  token?: string;
  done?: boolean;
  sources?: string[];
}
