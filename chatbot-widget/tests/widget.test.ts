/**
 * Unit tests verifying history management limits and SSE event parsing.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {
  clampContentByRole,
  filterValidTurns,
  prepareHistoryForBackend,
  MAX_USER_CHARS,
  MAX_ASSISTANT_CHARS,
  MAX_HISTORY_TURNS,
  MAX_TOTAL_HISTORY_CHARS,
} from "../src/services/historyManager.ts";
import {
  parseSSELine,
  getFriendlyErrorMessage,
} from "../src/services/chatService.ts";
import type { UIMessage } from "../src/types/chat.ts";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { SafeMarkdown } from "../src/components/SafeMarkdown.ts";

test("clampContentByRole enforces role ceilings", () => {
  const longUser = "u".repeat(1500);
  const clampedUser = clampContentByRole("user", longUser);
  assert.equal(clampedUser.length, MAX_USER_CHARS);

  const longAsst = "a".repeat(4000);
  const clampedAsst = clampContentByRole("assistant", longAsst);
  assert.equal(clampedAsst.length, MAX_ASSISTANT_CHARS);

  const shortMsg = "Short text";
  assert.equal(clampContentByRole("user", shortMsg), shortMsg);
});

test("filterValidTurns excludes empty or whitespace messages", () => {
  const messages: UIMessage[] = [
    { id: "1", role: "user", content: "Valid message" },
    { id: "2", role: "assistant", content: "   " },
  ];
  const valid = filterValidTurns(messages);
  assert.equal(valid.length, 1);
  assert.equal(valid[0].content, "Valid message");
});

test("prepareHistoryForBackend limits history to at most 10 turns", () => {
  const messages: UIMessage[] = Array.from({ length: 15 }, (_, i) => ({
    id: `m-${i}`,
    role: i % 2 === 0 ? "user" : "assistant",
    content: `Message turn ${i}`,
  }));

  const history = prepareHistoryForBackend(messages);
  assert.equal(history.length, MAX_HISTORY_TURNS);
  // Ensure newest turn (14) is present
  assert.equal(history[history.length - 1].content, "Message turn 14");
});

test("prepareHistoryForBackend drops oldest messages first when exceeding 12000 chars", () => {
  // Create 10 assistant messages of 2000 chars each = 20,000 chars (exceeds 12000 limit)
  const messages: UIMessage[] = Array.from({ length: 10 }, (_, i) => ({
    id: `m-${i}`,
    role: "assistant",
    content: `Turn-${i}-` + "x".repeat(1990),
  }));

  const history = prepareHistoryForBackend(messages);
  const totalChars = history.reduce((sum, m) => sum + m.content.length, 0);

  assert.ok(totalChars <= MAX_TOTAL_HISTORY_CHARS);
  // Total must be 6 messages of 2000 = 12000 chars
  assert.equal(history.length, 6);
  // Ensure the newest message is preserved
  assert.ok(history[history.length - 1].content.startsWith("Turn-9-"));
  // Ensure the oldest messages (Turn-0 through Turn-3) were dropped
  assert.ok(history[0].content.startsWith("Turn-4-"));
});

test("parseSSELine parses valid token and done events", () => {
  const tokenLine = 'data: {"token": "Namaste"}';
  const tokenParsed = parseSSELine(tokenLine);
  assert.deepEqual(tokenParsed, { token: "Namaste" });

  const doneLine = 'data: {"done": true, "sources": ["employee_handbook.md"]}';
  const doneParsed = parseSSELine(doneLine);
  assert.deepEqual(doneParsed, { done: true, sources: ["employee_handbook.md"] });

  assert.equal(parseSSELine(": heartbeat"), null);
  assert.equal(parseSSELine("invalid data format"), null);
});

test("getFriendlyErrorMessage maps HTTP statuses correctly", () => {
  assert.equal(getFriendlyErrorMessage(401), "Session expired. Please re-authenticate.");
  assert.equal(getFriendlyErrorMessage(403), "You do not have permission to access this resource.");
  assert.equal(getFriendlyErrorMessage(422), "Message is too long or invalid.");
  assert.equal(getFriendlyErrorMessage(429), "Too many requests. Please try again later.");
  assert.ok(getFriendlyErrorMessage(500).includes("unexpected error"));
});

test("image markdown does not produce an <img> element", () => {
  const markdown = "Here is an image: ![Company Logo](https://example.com/logo.png)";
  const html = renderToStaticMarkup(React.createElement(SafeMarkdown, { content: markdown }));
  assert.ok(!html.includes("<img"));
  assert.ok(!html.includes("<image"));
});

test("javascript: link is not rendered as a clickable link", () => {
  const markdown = "[Malicious Action](javascript:alert(document.cookie))";
  const html = renderToStaticMarkup(React.createElement(SafeMarkdown, { content: markdown }));
  assert.ok(!html.includes("<a"));
  assert.ok(!html.includes("javascript:"));
  assert.ok(html.includes("Malicious Action"));
});

test("data: link is not rendered as a clickable link", () => {
  const markdown = "[Dangerous Data](data:text/html,<script>alert(1)</script>)";
  const html = renderToStaticMarkup(React.createElement(SafeMarkdown, { content: markdown }));
  assert.ok(!html.includes("<a"));
  assert.ok(!html.includes("data:text/html"));
  assert.ok(html.includes("Dangerous Data"));
});

test("https: link is rendered as secure link with rel and target attributes", () => {
  const markdown = "[Official Handbook](https://intranet.company.internal/docs)";
  const html = renderToStaticMarkup(React.createElement(SafeMarkdown, { content: markdown }));
  assert.ok(html.includes('<a href="https://intranet.company.internal/docs" target="_blank" rel="noopener noreferrer nofollow">Official Handbook</a>'));
});

test("raw <script> and <img> HTML in the answer is shown as text, not executed", () => {
  const markdown = "<script>alert('xss')</script><img src=x onerror=alert(1)>";
  const html = renderToStaticMarkup(React.createElement(SafeMarkdown, { content: markdown }));
  // Raw HTML tags must not exist in output; they must be entity-escaped
  assert.ok(!html.includes("<script>"));
  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&lt;script&gt;"));
  assert.ok(html.includes("&lt;img"));
});
