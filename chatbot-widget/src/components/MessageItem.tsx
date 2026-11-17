/**
 * Renders an individual chat message with safe markdown, sources, and feedback buttons.
 */
import React from "react";
import { SafeMarkdown } from "./SafeMarkdown";
import { UIMessage } from "../types/chat";

interface MessageItemProps {
  message: UIMessage;
  onFeedback: (messageId: string, type: "up" | "down") => void;
}

export const MessageItem: React.FC<MessageItemProps> = ({ message, onFeedback }) => {
  const isUser = message.role === "user";

  return (
    <div
      className={`chat-msg chat-msg-${message.role}`}
      role="article"
      aria-label={`${message.role} message`}
    >
      <div className="chat-msg-bubble">
        {isUser ? (
          <p className="chat-msg-text">{message.content}</p>
        ) : (
          <div className="chat-msg-markdown">
            <SafeMarkdown content={message.content || (message.isStreaming ? "Thinking..." : "")} />
            {message.isStreaming && <span className="chat-typing-cursor" aria-hidden="true" />}
          </div>
        )}

        {message.error && (
          <div className="chat-msg-error" role="alert">
            {message.error}
          </div>
        )}

        {!isUser && !message.isStreaming && message.sources && message.sources.length > 0 && (
          <div className="chat-msg-sources" aria-label="Referenced sources">
            <span className="chat-sources-label">Sources:</span>
            <div className="chat-sources-list">
              {message.sources.map((src, idx) => (
                <span key={`${src}-${idx}`} className="chat-source-tag">
                  📄 {src}
                </span>
              ))}
            </div>
          </div>
        )}

        {!isUser && !message.isStreaming && !message.error && message.content && (
          <div className="chat-msg-feedback" aria-label="Message feedback">
            <button
              type="button"
              className={`feedback-btn ${message.feedback === "up" ? "active" : ""}`}
              onClick={() => onFeedback(message.id, "up")}
              aria-label="Helpful answer"
              title="Helpful"
            >
              👍
            </button>
            <button
              type="button"
              className={`feedback-btn ${message.feedback === "down" ? "active" : ""}`}
              onClick={() => onFeedback(message.id, "down")}
              aria-label="Unhelpful answer"
              title="Not helpful"
            >
              👎
            </button>
          </div>
        )}
      </div>
    </div>
  );
};
