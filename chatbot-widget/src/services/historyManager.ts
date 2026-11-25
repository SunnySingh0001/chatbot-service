/**
 * Manages chat history trimming and truncation matching backend schemas.py limits.
 */
import type { BackendChatMessage, UIMessage } from "../types/chat.ts";

export const MAX_USER_CHARS = 1000;
export const MAX_ASSISTANT_CHARS = 3000;
export const MAX_HISTORY_TURNS = 10;
export const MAX_TOTAL_HISTORY_CHARS = 12000;

/**
 * Truncates message content strictly to the allowed ceiling per role,
 * preserving trailing pagination footers on long assistant replies.
 */
export function clampContentByRole(role: "user" | "assistant", content: string): string {
  const limit = role === "user" ? MAX_USER_CHARS : MAX_ASSISTANT_CHARS;
  if (content.length <= limit) {
    return content;
  }
  if (role === "assistant" && limit > 700) {
    const marker = "\n...\n";
    const tailLen = 595;
    const headLen = limit - marker.length - tailLen;
    return `${content.slice(0, headLen)}${marker}${content.slice(-tailLen)}`;
  }
  return content.slice(0, limit);
}

/**
 * Filters and validates UI messages to role-compliant backend history entries.
 */
export function filterValidTurns(messages: UIMessage[]): BackendChatMessage[] {
  return messages
    .filter((m) => (m.role === "user" || m.role === "assistant") && m.content.trim().length > 0)
    .map((m) => ({
      role: m.role,
      content: clampContentByRole(m.role, m.content),
    }));
}

/**
 * Prepares and trims conversation history to fit within backend turn and character bounds.
 */
export function prepareHistoryForBackend(messages: UIMessage[]): BackendChatMessage[] {
  const valid = filterValidTurns(messages);
  // Take at most the last 10 messages
  const window = valid.slice(-MAX_HISTORY_TURNS);

  // Drop oldest messages first until total characters <= 12000
  let totalChars = window.reduce((acc, curr) => acc + curr.content.length, 0);
  while (totalChars > MAX_TOTAL_HISTORY_CHARS && window.length > 0) {
    const dropped = window.shift();
    if (dropped) {
      totalChars -= dropped.content.length;
    }
  }

  return window;
}
