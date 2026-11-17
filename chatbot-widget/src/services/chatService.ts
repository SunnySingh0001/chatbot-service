/**
 * Service to execute streaming POST /chat requests and parse SSE token events.
 */
import type { BackendChatMessage, SSETokenEvent } from "../types/chat.ts";

export interface StreamCallbacks {
  onToken: (token: string) => void;
  onDone: (sources: string[]) => void;
  onError: (errorMessage: string) => void;
}

/**
 * Returns user-friendly message corresponding to HTTP status code.
 */
export function getFriendlyErrorMessage(statusCode: number): string {
  switch (statusCode) {
    case 401:
      return "Session expired. Please re-authenticate.";
    case 403:
      return "You do not have permission to access this resource.";
    case 422:
      return "Message is too long or invalid.";
    case 429:
      return "Too many requests. Please try again later.";
    default:
      return "An unexpected error occurred while communicating with the assistant.";
  }
}

/**
 * Parses a single SSE data line into a structured token or done payload.
 */
export function parseSSELine(line: string): SSETokenEvent | null {
  const trimmed = line.trim();
  if (!trimmed.startsWith("data:")) {
    return null;
  }
  const jsonStr = trimmed.slice(5).trim();
  if (!jsonStr) {
    return null;
  }
  try {
    return JSON.parse(jsonStr) as SSETokenEvent;
  } catch {
    return null;
  }
}

/**
 * Streams chat completion tokens via POST /chat and dispatches parsed SSE events.
 */
export async function streamChatResponse(
  apiUrl: string,
  getToken: () => string | Promise<string>,
  message: string,
  history: BackendChatMessage[],
  callbacks: StreamCallbacks,
  signal: AbortSignal
): Promise<void> {
  const token = await getToken();
  if (!token || !token.trim()) {
    callbacks.onError("Authentication token missing. Please provide a valid JWT.");
    return;
  }

  const cleanBaseUrl = apiUrl.replace(/\/+$/, "");
  const endpoint = `${cleanBaseUrl}/chat`;

  let response: Response;
  try {
    response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token.trim()}`,
      },
      body: JSON.stringify({ message, history }),
      signal,
    });
  } catch (err: unknown) {
    if (signal.aborted) {
      return;
    }
    callbacks.onError("Unable to connect to the server. Please check your network and try again.");
    return;
  }

  if (!response.ok) {
    callbacks.onError(getFriendlyErrorMessage(response.status));
    return;
  }

  if (!response.body) {
    callbacks.onError("No response stream received from the server.");
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split(/\r?\n\r?\n/);
      buffer = parts.pop() ?? "";

      for (const part of parts) {
        const lines = part.split(/\r?\n/);
        for (const line of lines) {
          const event = parseSSELine(line);
          if (event) {
            if (event.token) {
              callbacks.onToken(event.token);
            }
            if (event.done) {
              callbacks.onDone(event.sources ?? []);
            }
          }
        }
      }
    }
  } catch (err: unknown) {
    if (signal.aborted) {
      return;
    }
    callbacks.onError("An error occurred while streaming the response.");
  }
}
