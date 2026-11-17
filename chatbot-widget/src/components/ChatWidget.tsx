/**
 * Main floating chat widget component providing bubble toggle, panel dialog, and streaming orchestration.
 */
import React, { useRef, useState } from "react";
import { ChatWidgetProps, UIMessage } from "../types/chat";
import { prepareHistoryForBackend } from "../services/historyManager";
import { streamChatResponse } from "../services/chatService";
import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";

export const ChatWidget: React.FC<ChatWidgetProps> = ({ apiUrl, getToken }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState<UIMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  const toggleOpen = () => {
    setIsOpen((prev) => !prev);
  };

  const handleFeedback = (messageId: string, type: "up" | "down") => {
    setMessages((prev) =>
      prev.map((msg) =>
        msg.id === messageId
          ? { ...msg, feedback: msg.feedback === type ? undefined : type }
          : msg
      )
    );
  };

  const handleStop = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsStreaming(false);
    setMessages((prev) =>
      prev.map((msg) => (msg.isStreaming ? { ...msg, isStreaming: false } : msg))
    );
  };

  const handleSend = async (questionText: string) => {
    if (isStreaming) {
      return;
    }

    const userMsgId = `user-${Date.now()}`;
    const assistantMsgId = `asst-${Date.now()}`;

    const userMsg: UIMessage = {
      id: userMsgId,
      role: "user",
      content: questionText,
    };

    const assistantPlaceholder: UIMessage = {
      id: assistantMsgId,
      role: "assistant",
      content: "",
      isStreaming: true,
    };

    // Calculate compliant history snapshot before adding current turn
    const backendHistory = prepareHistoryForBackend(messages);

    setMessages((prev) => [...prev, userMsg, assistantPlaceholder]);
    setIsStreaming(true);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    await streamChatResponse(
      apiUrl,
      getToken,
      questionText,
      backendHistory,
      {
        onToken: (token) => {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMsgId
                ? { ...msg, content: msg.content + token }
                : msg
            )
          );
        },
        onDone: (sources) => {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMsgId
                ? { ...msg, isStreaming: false, sources }
                : msg
            )
          );
          setIsStreaming(false);
        },
        onError: (errorMessage) => {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMsgId
                ? { ...msg, isStreaming: false, error: errorMessage }
                : msg
            )
          );
          setIsStreaming(false);
        },
      },
      controller.signal
    );
  };

  return (
    <div className="chat-widget-root">
      {isOpen && (
        <section
          className="chat-panel"
          role="dialog"
          aria-label="Employee Support Assistant"
          aria-modal="true"
        >
          <header className="chat-header">
            <div className="chat-header-title-group">
              <span className="chat-header-avatar">🤖</span>
              <div>
                <h2 className="chat-header-title">Employee Support Bot</h2>
                <span className="chat-header-status">● Online</span>
              </div>
            </div>
            <button
              type="button"
              className="chat-header-close-btn"
              onClick={toggleOpen}
              aria-label="Close chat window"
              title="Close chat"
            >
              ✕
            </button>
          </header>

          <MessageList messages={messages} onFeedback={handleFeedback} />

          <ChatInput onSend={handleSend} onStop={handleStop} isStreaming={isStreaming} />
        </section>
      )}

      <button
        type="button"
        className={`chat-bubble-btn ${isOpen ? "chat-bubble-open" : ""}`}
        onClick={toggleOpen}
        aria-label={isOpen ? "Close chat assistant" : "Open chat assistant"}
        aria-expanded={isOpen}
      >
        {isOpen ? "✕" : "💬"}
      </button>
    </div>
  );
};
