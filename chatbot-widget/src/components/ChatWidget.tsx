/**
 * Main floating chat widget component providing bubble toggle, panel dialog, session persistence, streaming orchestration, retry, and regeneration.
 */
import React, { useEffect, useRef, useState } from "react";
import { ChatWidgetProps, UIMessage } from "../types/chat";
import { prepareHistoryForBackend } from "../services/historyManager";
import { streamChatResponse } from "../services/chatService";
import { generateSessionTitle } from "../services/sessionStore";
import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";

export const ChatWidget: React.FC<ChatWidgetProps> = ({
  apiUrl,
  getToken,
  embedded = false,
  activePersonaName,
  activePersonaRole = "none",
  sessionId: propSessionId,
  initialMessages = [],
  onSessionChange,
  onNewChatTriggered,
  pendingPrompt = null,
  onPendingPromptConsumed,
  onPersistFeedback,
  isTemporary = false,
}) => {
  const [isOpen, setIsOpen] = useState(embedded);
  const [messages, setMessages] = useState<UIMessage[]>(initialMessages);
  const messagesRef = useRef<UIMessage[]>(initialMessages);
  const [, setSessionId] = useState<string | undefined>(propSessionId);
  const sessionIdRef = useRef<string | undefined>(propSessionId);
  const [isStreaming, setIsStreaming] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Sync backend session ID if updated externally without clobbering active messages mid-stream
  useEffect(() => {
    setSessionId(propSessionId);
    sessionIdRef.current = propSessionId;
  }, [propSessionId]);

  const updateMessages = (updater: (prev: UIMessage[]) => UIMessage[], notifyParent = false) => {
    const nextMsgs = updater(messagesRef.current);
    messagesRef.current = nextMsgs;
    setMessages(nextMsgs);
    if (notifyParent && onSessionChange) {
      onSessionChange({
        id: sessionIdRef.current || "",
        backendSessionId: sessionIdRef.current,
        messages: nextMsgs,
        title: generateSessionTitle(nextMsgs),
      });
    }
  };

  const toggleOpen = () => {
    setIsOpen((prev) => !prev);
  };

  const handleFeedback = (messageId: string, type: "up" | "down") => {
    if (!onPersistFeedback) return;
    let persistedType: "up" | "down" | null = type;
    updateMessages(
      (prev) =>
        prev.map((msg) => {
          if (msg.id !== messageId) return msg;
          const nextFeedback = msg.feedback === type ? undefined : type;
          persistedType = nextFeedback ?? null;
          return { ...msg, feedback: nextFeedback };
        }),
      true
    );
    void onPersistFeedback(messageId, persistedType);
  };

  const handleNewChat = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsStreaming(false);
    messagesRef.current = [];
    setMessages([]);
    setSessionId(undefined);
    sessionIdRef.current = undefined;
    onNewChatTriggered?.();
  };

  const handleStop = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsStreaming(false);
    updateMessages(
      (prev) => prev.map((msg) => (msg.isStreaming ? { ...msg, isStreaming: false } : msg)),
      true
    );
  };

  const handleSend = async (questionText: string) => {
    if (isStreaming) {
      return;
    }

    const now = new Date();
    const timeStr = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const userMsgId = `user-${Date.now()}`;
    const assistantMsgId = `asst-${Date.now()}`;

    const userMsg: UIMessage = {
      id: userMsgId,
      role: "user",
      content: questionText,
      timestamp: timeStr,
    };

    const assistantPlaceholder: UIMessage = {
      id: assistantMsgId,
      role: "assistant",
      content: "",
      isStreaming: true,
      timestamp: timeStr,
    };

    // Calculate compliant history snapshot before adding current turn
    const backendHistory = prepareHistoryForBackend(messagesRef.current);

    updateMessages((prev) => [...prev, userMsg, assistantPlaceholder], true);
    setIsStreaming(true);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    await streamChatResponse(
      apiUrl,
      getToken,
      questionText,
      backendHistory,
      {
        onSessionId: (newSid) => {
          sessionIdRef.current = newSid;
          setSessionId(newSid);
        },
        onToken: (token) => {
          updateMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMsgId
                ? { ...msg, content: msg.content + token }
                : msg
            )
          );
        },
        onDone: (sources) => {
          updateMessages(
            (prev) =>
              prev.map((msg) =>
                msg.id === assistantMsgId
                  ? { ...msg, isStreaming: false, sources }
                  : msg
              ),
            true
          );
          setIsStreaming(false);
        },
        onError: (errorMessage) => {
          updateMessages(
            (prev) =>
              prev.map((msg) =>
                msg.id === assistantMsgId
                  ? { ...msg, isStreaming: false, error: errorMessage }
                  : msg
              ),
            true
          );
          setIsStreaming(false);
        },
      },
      controller.signal,
      isTemporary ? undefined : sessionIdRef.current,
      isTemporary
    );
  };

  // Trigger queries dispatched externally (e.g., from SearchModal quick queries)
  useEffect(() => {
    if (pendingPrompt && !isStreaming) {
      onPendingPromptConsumed?.();
      void handleSend(pendingPrompt.text);
    }
  }, [pendingPrompt, isStreaming]);

  const handleRegenerate = async (targetMessageId: string) => {
    if (isStreaming) return;
    const asstIdx = messagesRef.current.findIndex((m) => m.id === targetMessageId);
    if (asstIdx <= 0) return;
    const userMsg = messagesRef.current[asstIdx - 1];
    if (!userMsg || userMsg.role !== "user") return;

    const questionText = userMsg.content;
    const priorHistory = messagesRef.current.slice(0, asstIdx - 1);
    const backendHistory = prepareHistoryForBackend(priorHistory);

    const now = new Date();
    const timeStr = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const newAssistantMsgId = `asst-${Date.now()}`;

    const assistantPlaceholder: UIMessage = {
      id: newAssistantMsgId,
      role: "assistant",
      content: "",
      isStreaming: true,
      timestamp: timeStr,
    };

    // Replace old assistant turn with placeholder
    updateMessages((prev) => [...prev.slice(0, asstIdx), assistantPlaceholder], true);
    setIsStreaming(true);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    await streamChatResponse(
      apiUrl,
      getToken,
      questionText,
      backendHistory,
      {
        onSessionId: (newSid) => {
          sessionIdRef.current = newSid;
          setSessionId(newSid);
        },
        onToken: (token) => {
          updateMessages((prev) =>
            prev.map((msg) =>
              msg.id === newAssistantMsgId
                ? { ...msg, content: msg.content + token }
                : msg
            )
          );
        },
        onDone: (sources) => {
          updateMessages(
            (prev) =>
              prev.map((msg) =>
                msg.id === newAssistantMsgId
                  ? { ...msg, isStreaming: false, sources }
                  : msg
              ),
            true
          );
          setIsStreaming(false);
        },
        onError: (errorMessage) => {
          updateMessages(
            (prev) =>
              prev.map((msg) =>
                msg.id === newAssistantMsgId
                  ? { ...msg, isStreaming: false, error: errorMessage }
                  : msg
              ),
            true
          );
          setIsStreaming(false);
        },
      },
      controller.signal,
      isTemporary ? undefined : sessionIdRef.current,
      isTemporary
    );
  };

  const handleRetry = (failedMessageId: string) => {
    handleRegenerate(failedMessageId);
  };

  if (embedded) {
    return (
      <div className="chat-widget-embedded">
        <section
          className={`chat-panel chat-panel-embedded ${
            messages.length === 0 ? "is-empty" : "has-messages"
          }`}
          role="region"
          aria-label="Fakhri AI Assistant Conversation"
        >
          <MessageList
            messages={messages}
            activeRole={activePersonaRole}
            activePersonaName={activePersonaName}
            isTemporary={isTemporary}
            onFeedback={onPersistFeedback ? handleFeedback : undefined}
            onSelectPrompt={handleSend}
            onRetry={handleRetry}
            onRegenerate={handleRegenerate}
          />
          <ChatInput onSend={handleSend} onStop={handleStop} isStreaming={isStreaming} />
        </section>
      </div>
    );
  }

  return (
    <div className="chat-widget-root">
      {isOpen && (
        <section
          className="chat-panel"
          role="dialog"
          aria-label="Fakhri AI Assistant"
          aria-modal="true"
        >
          <header className="chat-header">
            <div className="chat-header-left">
              <div className="chat-gpt-icon-badge">
                <span className="chat-gpt-sparkle">✨</span>
              </div>
              <div className="chat-header-meta">
                <div className="chat-header-title-row">
                  <h2 className="chat-header-title">Fakhri AI</h2>
                  <span className="chat-model-pill">Enterprise 4o</span>
                </div>
                <span className="chat-online-badge">
                  <span className="chat-online-dot" /> Online • Fakhri IT 2.0
                </span>
              </div>
            </div>
            <div className="chat-header-actions">
              <button
                type="button"
                className="chat-header-action-btn chat-new-chat-btn"
                onClick={handleNewChat}
                aria-label="Start new chat"
                title="New chat"
              >
                <span className="chat-new-chat-icon">✎</span> New
              </button>
              <button
                type="button"
                className="chat-header-action-btn chat-close-btn"
                onClick={toggleOpen}
                aria-label="Close chat window"
                title="Close chat"
              >
                ✕
              </button>
            </div>
          </header>

          <MessageList
            messages={messages}
            activeRole={activePersonaRole}
            activePersonaName={activePersonaName}
            isTemporary={isTemporary}
            onFeedback={onPersistFeedback ? handleFeedback : undefined}
            onSelectPrompt={handleSend}
            onRetry={handleRetry}
            onRegenerate={handleRegenerate}
          />

          <ChatInput onSend={handleSend} onStop={handleStop} isStreaming={isStreaming} />
        </section>
      )}

      <button
        type="button"
        className={`chat-bubble-launcher ${isOpen ? "chat-bubble-active" : ""}`}
        onClick={toggleOpen}
        aria-label={isOpen ? "Close Support Assistant" : "Open Support Assistant"}
      >
        {isOpen ? (
          <span className="chat-launcher-close-icon">✕</span>
        ) : (
          <div className="chat-launcher-icon-wrap">
            <span className="chat-launcher-sparkle">✨</span>
          </div>
        )}
      </button>
    </div>
  );
};
