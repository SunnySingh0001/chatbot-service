/**
 * Enterprise conversation history and session persistence management.
 * Provides date-based grouping, local storage caching, search indexing, and title derivation.
 */
import type { UIMessage } from "../types/chat.ts";

export interface StoredConversation {
  id: string; // Client-stable thread key
  backendSessionId?: string; // Server-issued UUID from POST /chat (X-Session-ID)
  title: string;
  role: string;
  createdAt: string; // ISO 8601 string
  updatedAt: string; // ISO 8601 string
  pinned?: boolean;
  messages: UIMessage[];
}

export type DateGroupLabel =
  | "Today"
  | "Yesterday"
  | "Previous 7 Days"
  | "Previous 30 Days"
  | "Older";

export const STORAGE_KEY = "fakhri_chat_sessions_v1";

/**
 * Calculates the appropriate date category for an ISO timestamp.
 */
export function getGroupingCategory(isoDateStr: string): DateGroupLabel {
  const date = new Date(isoDateStr);
  if (isNaN(date.getTime())) return "Older";

  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfYesterday = new Date(startOfToday.getTime() - 24 * 60 * 60 * 1000);
  const startOf7Days = new Date(startOfToday.getTime() - 7 * 24 * 60 * 60 * 1000);
  const startOf30Days = new Date(startOfToday.getTime() - 30 * 24 * 60 * 60 * 1000);

  if (date >= startOfToday) {
    return "Today";
  } else if (date >= startOfYesterday) {
    return "Yesterday";
  } else if (date >= startOf7Days) {
    return "Previous 7 Days";
  } else if (date >= startOf30Days) {
    return "Previous 30 Days";
  } else {
    return "Older";
  }
}

/**
 * Groups a list of stored conversations by Pinned state and date categories.
 */
export function groupConversationsByDate(conversations: StoredConversation[]): {
  pinned: StoredConversation[];
  groups: { label: DateGroupLabel; items: StoredConversation[] }[];
} {
  const pinned: StoredConversation[] = [];
  const unpinned: StoredConversation[] = [];

  for (const conv of conversations) {
    if (conv.pinned) {
      pinned.push(conv);
    } else {
      unpinned.push(conv);
    }
  }

  // Sort unpinned by updatedAt descending
  unpinned.sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());

  const categoryBuckets: Record<DateGroupLabel, StoredConversation[]> = {
    Today: [],
    Yesterday: [],
    "Previous 7 Days": [],
    "Previous 30 Days": [],
    Older: [],
  };

  for (const conv of unpinned) {
    const cat = getGroupingCategory(conv.updatedAt || conv.createdAt);
    categoryBuckets[cat].push(conv);
  }

  const order: DateGroupLabel[] = [
    "Today",
    "Yesterday",
    "Previous 7 Days",
    "Previous 30 Days",
    "Older",
  ];

  const groups = order
    .filter((label) => categoryBuckets[label].length > 0)
    .map((label) => ({
      label,
      items: categoryBuckets[label],
    }));

  return { pinned, groups };
}

/**
 * Derives a clean conversation title from the first user question or default fallback.
 */
export function generateSessionTitle(messages: UIMessage[]): string {
  const firstUserMsg = messages.find((m) => m.role === "user" && m.content.trim().length > 0);
  if (!firstUserMsg) return "New Conversation";

  const raw = firstUserMsg.content.trim().replace(/\s+/g, " ");
  if (raw.length <= 36) return raw;
  return raw.slice(0, 36).trim() + "...";
}

/**
 * Validates a user-provided conversation title.
 */
export function validateTitle(title: string): { valid: boolean; error?: string } {
  const trimmed = title.trim();
  if (trimmed.length === 0) {
    return { valid: false, error: "Title cannot be empty or whitespace only." };
  }
  if (trimmed.length > 80) {
    return { valid: false, error: "Title cannot exceed 80 characters." };
  }
  return { valid: true };
}

/**
 * Formats an ISO timestamp for display.
 */
export function formatConversationTime(isoDateStr: string): string {
  const d = new Date(isoDateStr);
  if (isNaN(d.getTime())) return "";

  const now = new Date();
  const isToday =
    d.getDate() === now.getDate() &&
    d.getMonth() === now.getMonth() &&
    d.getFullYear() === now.getFullYear();

  if (isToday) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  const isCurrentYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString([], {
    month: "short",
    day: "numeric",
    ...(isCurrentYear ? {} : { year: "numeric" }),
  });
}

/**
 * Loads stored conversation history from localStorage with safety fallback.
 */
export function loadStoredSessions(): StoredConversation[] {
  try {
    const raw = typeof window !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null;
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      return parsed.filter((item) => item && typeof item.id === "string" && item.title);
    }
  } catch {
    // Storage access fallback
  }
  return [];
}

/**
 * Persists stored conversations to localStorage.
 */
export function saveStoredSessions(sessions: StoredConversation[]): void {
  try {
    if (typeof window !== "undefined") {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
    }
  } catch {
    // Storage quota or error fallback
  }
}

export interface SearchResultItem {
  conv: StoredConversation;
  matchType: "title" | "content";
  matchSnippet?: string;
}

/**
 * Searches across conversation titles and message contents with snippet extraction.
 */
export function searchConversations(
  conversations: StoredConversation[],
  query: string
): SearchResultItem[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];

  const results: SearchResultItem[] = [];

  for (const conv of conversations) {
    if (conv.title.toLowerCase().includes(q)) {
      results.push({ conv, matchType: "title" });
      continue;
    }

    // Search message contents
    const matchingMsg = conv.messages.find((m) => m.content.toLowerCase().includes(q));
    if (matchingMsg) {
      const idx = matchingMsg.content.toLowerCase().indexOf(q);
      const start = Math.max(0, idx - 30);
      const end = Math.min(matchingMsg.content.length, idx + q.length + 50);
      const snippet = (start > 0 ? "..." : "") + matchingMsg.content.slice(start, end).trim() + "...";
      results.push({ conv, matchType: "content", matchSnippet: snippet });
    }
  }

  return results;
}
