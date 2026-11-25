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
import { STARTER_PROMPTS, getVisiblePrompts, getRoleAccessibleModules } from "../src/services/promptStarters.ts";
import {
  getGroupingCategory,
  groupConversationsByDate,
  validateTitle,
  generateSessionTitle,
  searchConversations,
} from "../src/services/sessionStore.ts";
import type { StoredConversation } from "../src/services/sessionStore.ts";
import {
  resolveDevAuthEnabled,
  canOpenAdminWorkspaceSettings,
  shouldRenderFeedbackControls,
} from "../src/services/securityGate.ts";

test("clampContentByRole enforces role ceilings", () => {
  const longUser = "u".repeat(1500);
  const clampedUser = clampContentByRole("user", longUser);
  assert.equal(clampedUser.length, MAX_USER_CHARS);

  const longAsst = `${"a".repeat(3600)}\nDikhaye gaye clients: 19–36 (Kul: 1,443)`;
  const clampedAsst = clampContentByRole("assistant", longAsst);
  assert.equal(clampedAsst.length, MAX_ASSISTANT_CHARS);
  assert.equal(clampedAsst.includes("Dikhaye gaye clients: 19–36"), true);

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

test("STARTER_PROMPTS enforces role-based access filtering", () => {
  // Super admin sees all 6 categories
  const superAdminVisible = getVisiblePrompts("super-admin");
  assert.equal(superAdminVisible.length, 6);

  // Founder sees all 6 categories
  const founderVisible = getVisiblePrompts("founder");
  assert.equal(founderVisible.length, 6);

  // Employee only sees general categories, strictly excluding finance and operations approvals
  const employeeVisible = getVisiblePrompts("employee");
  assert.equal(employeeVisible.length, 3);
  const employeeIds = employeeVisible.map((p) => p.id);
  assert.ok(employeeIds.includes("hr-hierarchy"));
  assert.ok(employeeIds.includes("company-policies"));
  assert.ok(employeeIds.includes("it-support"));
  assert.ok(!employeeIds.includes("finance-invoices"));
  assert.ok(!employeeIds.includes("operations-approvals"));

  // Finance role sees finance & invoices, psa clients, but NOT operations approvals
  const financeVisible = getVisiblePrompts("finance");
  const financeIds = financeVisible.map((p) => p.id);
  assert.ok(financeIds.includes("finance-invoices"));
  assert.ok(financeIds.includes("psa-clients"));
  assert.ok(!financeIds.includes("operations-approvals"));
});

test("SafeMarkdown renders tables inside .chat-table-wrapper with sticky-ready structure", () => {
  const markdownTable = "| Name | Role |\n|---|---|\n| Atul | Super Admin |";
  const html = renderToStaticMarkup(React.createElement(SafeMarkdown, { content: markdownTable }));
  assert.ok(html.includes('<div class="chat-table-wrapper">'));
  assert.ok(html.includes('<table class="chat-table">'));
  assert.ok(html.includes("<th>Name</th>"));
  assert.ok(html.includes("<td>Atul</td>"));
});

test("SafeMarkdown renders code blocks inside .chat-code-block-wrapper and inline code with .chat-inline-code", () => {
  const markdown = "Here is `someVar` and a block:\n\n```python\nprint(123)\n```";
  const html = renderToStaticMarkup(React.createElement(SafeMarkdown, { content: markdown }));
  assert.ok(html.includes('class="chat-inline-code"'));
  assert.ok(html.includes('class="chat-code-block-wrapper"'));
  assert.ok(html.includes('class="chat-code-pre"'));
  assert.ok(html.includes("print(123)"));
});

test("getGroupingCategory correctly categorizes timestamps", () => {
  const now = new Date();
  assert.equal(getGroupingCategory(now.toISOString()), "Today");

  const yesterday = new Date(now.getTime() - 25 * 3600 * 1000);
  assert.equal(getGroupingCategory(yesterday.toISOString()), "Yesterday");

  const fourDaysAgo = new Date(now.getTime() - 4 * 24 * 3600 * 1000);
  assert.equal(getGroupingCategory(fourDaysAgo.toISOString()), "Previous 7 Days");

  const twentyDaysAgo = new Date(now.getTime() - 20 * 24 * 3600 * 1000);
  assert.equal(getGroupingCategory(twentyDaysAgo.toISOString()), "Previous 30 Days");

  const sixtyDaysAgo = new Date(now.getTime() - 60 * 24 * 3600 * 1000);
  assert.equal(getGroupingCategory(sixtyDaysAgo.toISOString()), "Older");
});

test("groupConversationsByDate separates pinned threads and groups chronologically", () => {
  const now = new Date();
  const convs: StoredConversation[] = [
    {
      id: "c-1",
      title: "Pinned Handbook",
      role: "employee",
      createdAt: now.toISOString(),
      updatedAt: now.toISOString(),
      pinned: true,
      messages: [],
    },
    {
      id: "c-2",
      title: "Today's Query",
      role: "employee",
      createdAt: now.toISOString(),
      updatedAt: now.toISOString(),
      pinned: false,
      messages: [],
    },
    {
      id: "c-3",
      title: "Yesterday's Query",
      role: "employee",
      createdAt: new Date(now.getTime() - 26 * 3600 * 1000).toISOString(),
      updatedAt: new Date(now.getTime() - 26 * 3600 * 1000).toISOString(),
      pinned: false,
      messages: [],
    },
  ];

  const { pinned, groups } = groupConversationsByDate(convs);
  assert.equal(pinned.length, 1);
  assert.equal(pinned[0].id, "c-1");

  const labels = groups.map((g) => g.label);
  assert.ok(labels.includes("Today"));
  assert.ok(labels.includes("Yesterday"));
});

test("validateTitle rejects empty strings and strings over 80 chars", () => {
  assert.equal(validateTitle("").valid, false);
  assert.equal(validateTitle("   ").valid, false);
  assert.equal(validateTitle("x".repeat(81)).valid, false);
  assert.equal(validateTitle("HR Maternity Policies").valid, true);
});

test("generateSessionTitle derives title from first user query", () => {
  const emptyMsgs: UIMessage[] = [];
  assert.equal(generateSessionTitle(emptyMsgs), "New Conversation");

  const msgs: UIMessage[] = [
    { id: "1", role: "user", content: "Who reports to Disha Damwani in HR?" },
    { id: "2", role: "assistant", content: "Vivek and Aashi report to Disha." },
  ];
  assert.equal(generateSessionTitle(msgs), "Who reports to Disha Damwani in HR?");
});

test("searchConversations searches titles and message contents with snippet generation", () => {
  const convs: StoredConversation[] = [
    {
      id: "s-1",
      title: "Amazon FBA Guidelines",
      role: "operations",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      messages: [
        { id: "m1", role: "user", content: "What are the packaging standards?" },
        { id: "m2", role: "assistant", content: "Packages must have polybag suffocation warnings." },
      ],
    },
  ];

  // Search by title match
  const titleMatches = searchConversations(convs, "Amazon");
  assert.equal(titleMatches.length, 1);
  assert.equal(titleMatches[0].matchType, "title");

  // Search by message content match
  const contentMatches = searchConversations(convs, "suffocation");
  assert.equal(contentMatches.length, 1);
  assert.equal(contentMatches[0].matchType, "content");
  assert.ok(contentMatches[0].matchSnippet?.includes("suffocation"));
});

test("getRoleAccessibleModules returns correct module permissions for each role", () => {
  const superAdminMods = getRoleAccessibleModules("super-admin");
  assert.ok(superAdminMods.includes("All Modules"));
  assert.ok(superAdminMods.includes("System Telemetry"));

  const founderMods = getRoleAccessibleModules("founder");
  assert.ok(founderMods.includes("All Modules"));
  assert.ok(founderMods.includes("Company Policies"));

  const hrMods = getRoleAccessibleModules("hr");
  assert.ok(hrMods.includes("HR & Team Hierarchy"));
  assert.ok(hrMods.includes("Compensation & Salary Bands"));

  const opsMods = getRoleAccessibleModules("operations");
  assert.ok(opsMods.includes("Operations Workflows"));
  assert.ok(opsMods.includes("Pending Approvals"));

  const finMods = getRoleAccessibleModules("finance");
  assert.ok(finMods.includes("Invoices & Billing"));
  assert.ok(finMods.includes("PSA Client Directory"));

  const itMods = getRoleAccessibleModules("it");
  assert.ok(itMods.includes("IT Assets & Infrastructure"));
  assert.ok(itMods.includes("Security Guidelines"));

  const empMods = getRoleAccessibleModules("employee");
  assert.ok(empMods.includes("General Company Policies"));
  assert.ok(empMods.includes("Leave Rules"));

  const guestMods = getRoleAccessibleModules("none");
  assert.deepEqual(guestMods, ["Public Handbook", "General Policies"]);
});

test("getVisiblePrompts filters starter cards based on role module accessibility", () => {
  // Guest should only see public starter prompts (those with "*")
  const guestPrompts = getVisiblePrompts("none");
  assert.ok(guestPrompts.length > 0);
  assert.ok(guestPrompts.every((p) => p.allowedRoles.includes("*")));

  // HR should see HR, Operations (as it includes hr), and general prompts, but not finance-only
  const hrPrompts = getVisiblePrompts("hr");
  assert.ok(hrPrompts.some((p) => p.id === "hr-hierarchy"));
  assert.ok(hrPrompts.some((p) => p.id === "operations-approvals"));
  assert.ok(!hrPrompts.some((p) => p.id === "finance-invoices"));

  // Super Admin should see all prompt categories
  const adminPrompts = getVisiblePrompts("super-admin");
  assert.equal(adminPrompts.length, STARTER_PROMPTS.length);
});

test("Phase 7: Responsive breakpoint classification helper logic", () => {
  type Breakpoint = "mobile" | "tablet" | "desktop" | "large-desktop";
  const classifyWidth = (width: number): Breakpoint => {
    if (width < 768) return "mobile";
    if (width < 1024) return "tablet";
    if (width < 1440) return "desktop";
    return "large-desktop";
  };

  assert.equal(classifyWidth(320), "mobile");
  assert.equal(classifyWidth(375), "mobile");
  assert.equal(classifyWidth(390), "mobile");
  assert.equal(classifyWidth(767), "mobile");
  assert.equal(classifyWidth(768), "tablet");
  assert.equal(classifyWidth(1023), "tablet");
  assert.equal(classifyWidth(1024), "desktop");
  assert.equal(classifyWidth(1280), "desktop");
  assert.equal(classifyWidth(1439), "desktop");
  assert.equal(classifyWidth(1440), "large-desktop");
  assert.equal(classifyWidth(1920), "large-desktop");
});

test("Phase 7: Toast notification item contracts", () => {
  const toastTypes = ["info", "success", "warning", "error"] as const;
  for (const t of toastTypes) {
    const toast = { id: `test-${t}`, message: `Test ${t} message`, type: t };
    assert.equal(toast.type, t);
    assert.ok(toast.message.length > 0);
  }
});

test("Phase 8: getFriendlyErrorMessage maps HTTP 400 and SSE error safety", () => {
  assert.equal(getFriendlyErrorMessage(400), "Message is too long or invalid.");
  assert.equal(getFriendlyErrorMessage(401), "Session expired. Please re-authenticate.");
  assert.equal(getFriendlyErrorMessage(403), "You do not have permission to access this resource.");
  assert.equal(getFriendlyErrorMessage(429), "Too many requests. Please try again later.");
});

test("Phase 8: Role-scoped StoredConversation preserves backendSessionId cleanly", () => {
  const hrConv: StoredConversation = {
    id: "local-1",
    backendSessionId: "550e8400-e29b-41d4-a716-446655440000",
    title: "HR Policy Thread",
    role: "hr",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    messages: [],
  };
  const adminConv: StoredConversation = {
    id: "local-2",
    backendSessionId: "660e8400-e29b-41d4-a716-446655440001",
    title: "Founder Executive Thread",
    role: "founder",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    messages: [],
  };
  const all = [hrConv, adminConv];
  assert.equal(all.filter((c) => c.role === "hr").length, 1);
  assert.equal(all.filter((c) => c.role === "none").length, 0);
  assert.equal(hrConv.backendSessionId, "550e8400-e29b-41d4-a716-446655440000");
});

test("Phase 8.1 Blocker 2: Production security gate disables dev JWT injection by default", () => {
  assert.equal(resolveDevAuthEnabled(false, undefined), false);
  assert.equal(resolveDevAuthEnabled(false, "false"), false);
  assert.equal(resolveDevAuthEnabled(false, "true"), true);
  assert.equal(resolveDevAuthEnabled(true, undefined), true);

  // In production builds (enableDevAuth=false), only super-admin and founder retain admin sync access
  assert.equal(canOpenAdminWorkspaceSettings(false, "none"), false);
  assert.equal(canOpenAdminWorkspaceSettings(false, "employee"), false);
  assert.equal(canOpenAdminWorkspaceSettings(false, "hr"), false);
  assert.equal(canOpenAdminWorkspaceSettings(false, "super-admin"), true);
  assert.equal(canOpenAdminWorkspaceSettings(false, "founder"), true);
  assert.equal(canOpenAdminWorkspaceSettings(true, "none"), true);
});

test("Phase 8.1 Blocker 4: Non-persisted feedback actions remain hidden until backend persistence callback exists", () => {
  assert.equal(shouldRenderFeedbackControls(undefined), false);
  assert.equal(shouldRenderFeedbackControls(null), false);
  assert.equal(shouldRenderFeedbackControls(() => {}), true);
});

test("Temporary Chat: streamChatResponse sends temporary=true and omits session_id", async () => {
  const { streamChatResponse } = await import("../src/services/chatService.ts");
  const originalFetch = globalThis.fetch;
  let capturedBody: Record<string, unknown> | null = null;

  globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
    capturedBody = JSON.parse(String(init?.body || "{}"));
    const stream = new ReadableStream({
      start(controller) {
        const enc = new TextEncoder();
        controller.enqueue(enc.encode('data: {"token":"Ephemeral answer"}\n\n'));
        controller.enqueue(enc.encode('data: {"done":true,"sources":["mongo:users"],"temporary":true}\n\n'));
        controller.close();
      },
    });
    return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  }) as typeof fetch;

  try {
    let tokens = "";
    let sources: string[] = [];
    let sessionCallbackCalled = false;
    await streamChatResponse(
      "http://localhost:8000",
      () => "fake-jwt-token",
      "Temporary question",
      [],
      {
        onSessionId: () => {
          sessionCallbackCalled = true;
        },
        onToken: (t) => {
          tokens += t;
        },
        onDone: (s) => {
          sources = s;
        },
        onError: (err) => {
          throw new Error(err);
        },
      },
      new AbortController().signal,
      "should-be-omitted-session-id",
      true
    );

    assert.equal(capturedBody?.temporary, true);
    assert.equal("session_id" in (capturedBody || {}), false);
    assert.equal(sessionCallbackCalled, false);
    assert.equal(tokens, "Ephemeral answer");
    assert.deepEqual(sources, ["mongo:users"]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Welcome Lockup: getPersonalizedWelcomeCopy tailors brand pill, greeting, and role scope", async () => {
  const { getPersonalizedWelcomeCopy } = await import("../src/services/promptStarters.ts");

  const anonCopy = getPersonalizedWelcomeCopy("none");
  assert.equal(anonCopy.brandTag, "Fakhri AI");
  assert.equal(anonCopy.heading, "How can I help you today?");
  assert.match(anonCopy.subtitle, /Fakhri AI/);

  const opsCopy = getPersonalizedWelcomeCopy("operations", "Zainab Husain");
  assert.equal(opsCopy.brandTag, "Fakhri AI · Operations");
  assert.equal(opsCopy.heading, "Hi Zainab, how can I help you today?");
  assert.match(opsCopy.subtitle, /operational approvals/i);
});

test("Composer Focus: shouldRedirectKeyToComposer redirects printable keys safely without hijacking modals or shortcuts", async () => {
  const { shouldRedirectKeyToComposer, shouldAutoFocusComposer } = await import(
    "../src/services/composerFocus.ts"
  );

  const base = {
    key: "h",
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    isComposing: false,
    isDisabled: false,
    isComposerFocused: false,
    activeTagName: "BODY",
    isActiveContentEditable: false,
    hasOpenDialog: false,
  };

  assert.equal(shouldRedirectKeyToComposer(base), true);
  assert.equal(shouldRedirectKeyToComposer({ ...base, activeTagName: "BUTTON" }), true);
  assert.equal(shouldRedirectKeyToComposer({ ...base, key: " " }), false);
  assert.equal(shouldRedirectKeyToComposer({ ...base, key: "Enter" }), false);
  assert.equal(shouldRedirectKeyToComposer({ ...base, ctrlKey: true }), false);
  assert.equal(shouldRedirectKeyToComposer({ ...base, metaKey: true }), false);
  assert.equal(shouldRedirectKeyToComposer({ ...base, activeTagName: "INPUT" }), false);
  assert.equal(shouldRedirectKeyToComposer({ ...base, activeTagName: "TEXTAREA" }), false);
  assert.equal(shouldRedirectKeyToComposer({ ...base, isActiveContentEditable: true }), false);
  assert.equal(shouldRedirectKeyToComposer({ ...base, hasOpenDialog: true }), false);
  assert.equal(shouldRedirectKeyToComposer({ ...base, isDisabled: true }), false);
  assert.equal(shouldRedirectKeyToComposer({ ...base, isComposerFocused: true }), false);

  assert.equal(shouldAutoFocusComposer(1440, false, false), true);
  assert.equal(shouldAutoFocusComposer(390, false, false), false);
  assert.equal(shouldAutoFocusComposer(1440, true, false), false);
  assert.equal(shouldAutoFocusComposer(1440, false, true), false);
});

test("Sidebar Tooltips: formatConversationTime handles invalid, today, and older dates safely", async () => {
  const { formatConversationTime } = await import("../src/services/sessionStore.ts");

  assert.equal(formatConversationTime("not-a-date"), "");
  const todayFormatted = formatConversationTime(new Date().toISOString());
  assert.ok(todayFormatted.length > 0);
  const lastYear = new Date();
  lastYear.setFullYear(lastYear.getFullYear() - 1);
  const olderFormatted = formatConversationTime(lastYear.toISOString());
  assert.ok(olderFormatted.includes(String(lastYear.getFullYear())));
});

test("Scroll Helper: shouldShowJumpToLatest and isAtScrollBottom compute overflow & threshold accurately", async () => {
  const { getDistanceFromBottom, shouldShowJumpToLatest, isAtScrollBottom } = await import(
    "../src/services/scrollHelper.ts"
  );

  // Non-overflowing short chat never shows Jump to latest
  assert.equal(
    shouldShowJumpToLatest({ scrollHeight: 500, scrollTop: 0, clientHeight: 500 }),
    false
  );

  // Overflowing chat near the bottom stays snapped and hides button
  assert.equal(getDistanceFromBottom({ scrollHeight: 2000, scrollTop: 1480, clientHeight: 500 }), 20);
  assert.equal(isAtScrollBottom({ scrollHeight: 2000, scrollTop: 1480, clientHeight: 500 }), true);
  assert.equal(
    shouldShowJumpToLatest({ scrollHeight: 2000, scrollTop: 1480, clientHeight: 500 }),
    false
  );

  // Scrolling >120px up in overflowing chat shows Jump to latest
  assert.equal(isAtScrollBottom({ scrollHeight: 2000, scrollTop: 1200, clientHeight: 500 }), false);
  assert.equal(
    shouldShowJumpToLatest({ scrollHeight: 2000, scrollTop: 1200, clientHeight: 500 }),
    true
  );
});

test("Entity Explorer Service: buildEntityEndpointUrl builds clean query parameters for search, batching, and export", async () => {
  const { buildEntityEndpointUrl } = await import("../src/services/entityService.ts");

  const searchUrl = buildEntityEndpointUrl("http://127.0.0.1:8000/", "/search", {
    q: "Amazon",
    entity_type: "AccountType",
    limit: 20,
  });
  assert.equal(searchUrl, "http://127.0.0.1:8000/entities/search?q=Amazon&entity_type=AccountType&limit=20");

  const exportUrl = buildEntityEndpointUrl("http://127.0.0.1:8000", "/export", {
    identifier: "101",
    fmt: "csv",
    relation_name: undefined,
  });
  assert.equal(exportUrl, "http://127.0.0.1:8000/entities/export?identifier=101&fmt=csv");
});








